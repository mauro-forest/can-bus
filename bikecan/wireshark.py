"""Generating dbc/ebike.lua, a Wireshark dissector, from dbc/signals.toml.

Wireshark reads `candump` logs natively -- `tshark -r can0.log` works on a
session directory with no conversion -- but it knows nothing about what the
payloads mean. This module writes a Lua dissector so it does.

The dissector is generated from `signals.toml` and not from the generated
`ebike.dbc`, for the same reason `ebike.dbc` is generated from the TOML: the
confidence level and the evidence are the part that matters, and DBC has
nowhere to put a confidence level. Going through the DBC would throw both away
before they reached Lua. Edit the TOML; regenerate:

    uv run python -m bikecan.wireshark

Every signal below `confirmed` shows its level beside the value in the packet
detail, so a guess never reads like a measurement:

    HeadLightOn: 1
    HeadLightSource: 2 [hypothesis]

BIT NUMBERING

Same two conventions as `bikecan.dbc`, and the same trap. `big_endian`
start_bit counts MSB-first from byte 0; `little_endian` start_bit is the DBC
position of the least significant bit, which for a byte-aligned value is
8 * byte_index. The Lua helpers `be()` and `le()` implement one each, and the
generator refuses to emit a little-endian signal that is not byte-aligned
rather than produce something that decodes plausible-looking nonsense.
"""

from __future__ import annotations

from pathlib import Path

from bikecan.dbc import CONFIDENCE_LEVELS, DBC_DIR, SIGNALS_TOML, load_signals

OUTPUT_LUA = DBC_DIR / "ebike.lua"

PROTOCOL = "ebike"
# A message with no signals -- Heartbeat_1000 has none, its payload never
# varies -- would otherwise be reachable only by raw identifier. This field
# gives every message a name to filter on: ebike.message == "Heartbeat_1000".
MESSAGE_FIELD = "f_message"
PROTOCOL_DESCRIPTION = "Forest ebike CAN"
COLUMN_NAME = "eBike"


def lua_string(text: str) -> str:
    """Quote a Python string as a Lua string literal, verbatim."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def lua_prose(text: str) -> str:
    """Quote prose as a Lua string: whitespace collapsed onto one line."""
    return lua_string(" ".join(text.split()))


def summarise(text: str, limit: int = 150) -> str:
    """Shorten prose to fit a Wireshark tree label.

    Wireshark truncates an item label at 240 characters and prefixes what is
    left with a mangled marker, so a comment cut by Wireshark reads worse than
    one cut deliberately. The full text is in signals.toml either way.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit].rsplit(" ", 1)[0].rstrip(",;:.-")
    return f"{cut} ... (dbc/signals.toml)"


def lua_number(value) -> str:
    """Emit a number the way the TOML wrote it, so 0.1 stays 0.1."""
    if isinstance(value, bool):
        raise TypeError(f"expected a number, got {value!r}")
    if isinstance(value, int):
        return str(value)
    return repr(float(value))


def field_abbrev(message_name: str, signal_name: str) -> str:
    """Display-filter name. Wireshark filters are case-sensitive; lowercase is
    the convention, so `ebike.headlight_3606.headlighton` is what you type."""
    return f"{PROTOCOL}.{message_name.lower()}.{signal_name.lower()}"


def variable_name(message_name: str, signal_name: str) -> str:
    return f"f_{message_name.lower()}_{signal_name.lower()}"


def byte_span(signal: dict) -> tuple[int, int]:
    """The bytes a signal occupies, as (first, count).

    Both conventions land on the same answer here because every start_bit in
    signals.toml is byte-aligned, but they get there differently: MSB-first
    counts bits forwards from byte 0, little-endian names the LSB's position.
    """
    start = signal["start_bit"]
    length = signal["length"]
    if signal.get("byte_order", "big_endian") == "big_endian":
        first = start // 8
        last = (start + length - 1) // 8
        return first, last - first + 1
    return start // 8, length // 8


def validate(message: dict, signal: dict) -> None:
    name = f"{message['name']}.{signal['name']}"
    confidence = signal.get("confidence", "hypothesis")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(
            f"{name}: confidence {confidence!r} is not one of {CONFIDENCE_LEVELS}"
        )
    if signal.get("choices"):
        raise ValueError(f"{name}: choices are not supported by the Lua generator yet")
    if signal.get("is_float"):
        raise ValueError(f"{name}: raw floats are not supported by the Lua generator yet")

    length = signal["length"]
    if length > 32:
        # Wireshark's bitfield() returns a UInt64 above 32 bits and the
        # arithmetic below would silently stop working. Nothing in signals.toml
        # is that wide; fail loudly if something ever is.
        raise ValueError(f"{name}: signals wider than 32 bits are not supported")

    if signal.get("byte_order", "big_endian") == "little_endian":
        if signal["start_bit"] % 8 or length % 8:
            raise ValueError(
                f"{name}: little_endian signals must be byte-aligned and a whole "
                f"number of bytes (start_bit {signal['start_bit']}, length {length})"
            )


def field_declaration(message: dict, signal: dict) -> str:
    """The ProtoField line for one signal.

    A signal whose scale or offset is fractional has to be a double: the field
    carries the physical value, so that a display filter on it compares against
    millivolts and not against raw bytes.
    """
    scale = signal.get("scale", 1)
    offset = signal.get("offset", 0)
    fractional = not (
        isinstance(scale, int) or float(scale).is_integer()
    ) or not (isinstance(offset, int) or float(offset).is_integer())

    abbrev = lua_string(field_abbrev(message["name"], signal["name"]))
    label = lua_string(signal["name"])
    if fractional:
        constructor = f"ProtoField.double({abbrev}, {label})"
    elif signal.get("signed", False):
        constructor = f"ProtoField.int32({abbrev}, {label}, base.DEC)"
    else:
        constructor = f"ProtoField.uint32({abbrev}, {label}, base.DEC)"
    return f"local {variable_name(message['name'], signal['name'])} = {constructor}"


def value_expression(signal: dict) -> str:
    """The Lua expression that turns the payload into the physical value."""
    signed = "true" if signal.get("signed", False) else "false"
    if signal.get("byte_order", "big_endian") == "big_endian":
        raw = f"be(tvb, {signal['start_bit']}, {signal['length']}, {signed})"
    else:
        first, count = byte_span(signal)
        raw = f"le(tvb, {first}, {count}, {signed})"

    scale = signal.get("scale", 1)
    offset = signal.get("offset", 0)
    expression = raw
    if scale != 1:
        expression = f"{expression} * {lua_number(scale)}"
    if offset != 0:
        expression = f"{expression} + {lua_number(offset)}"
    return expression


def suffix(signal: dict) -> str:
    """What is appended after the value: the unit, then the confidence.

    `confirmed` is left unmarked. Anything else is marked, every time it is
    displayed, because the whole point of the confidence levels is that a
    reader can tell a measurement from a guess without opening signals.toml.
    """
    parts = []
    unit = signal.get("unit")
    if unit:
        parts.append(f" {unit}")
    confidence = signal.get("confidence", "hypothesis")
    if confidence != "confirmed":
        parts.append(f" [{confidence}]")
    return "".join(parts)


HEADER = f'''\
-- Wireshark dissector for the Forest ebike CAN bus.
--
-- GENERATED by bikecan/wireshark.py from dbc/signals.toml. Do not edit this
-- file: the evidence for every claim lives in the TOML, and hand-editing here
-- loses it. Regenerate with:
--
--     uv run python -m bikecan.wireshark
--
-- USAGE
--   Wireshark reads candump logs directly, so no conversion is needed:
--
--     tshark -X lua_script:dbc/ebike.lua -r data/sessions/<session>/can0.log
--
--   For the GUI, copy or symlink this file into the personal plugin folder
--   (Help > About Wireshark > Folders shows where; usually
--   ~/.local/lib/wireshark/plugins on Linux, ~/.config/wireshark/plugins on
--   macOS) and reload with Analyze > Reload Lua Plugins.
--
-- CONFIDENCE
--   Signals below "confirmed" show their level beside the value. A value with
--   no marker is tied to a measurement or a commanded action; one marked
--   [probable] or [hypothesis] is not. Read dbc/signals.toml for the evidence.

local {PROTOCOL} = Proto({lua_string(PROTOCOL)}, {lua_string(PROTOCOL_DESCRIPTION)})

local can_id = Field.new("can.id")
local can_extended = Field.new("can.flags.xtd")

-- The message name, so that every message can be filtered by the name it
-- displays and not just by its identifier. Generated rather than read from the
-- payload: the name is ours, not the bus's.
local {MESSAGE_FIELD} = ProtoField.string("{PROTOCOL}.message", "Message")

-- Read `length` bits starting at MSB-first bit index `start`: bit 0 is the top
-- bit of byte 0, which is how signals.toml numbers big_endian signals and how
-- discover.bit_detail() prints them.
local function be(tvb, start, length, signed)
  local first = math.floor(start / 8)
  local last = math.floor((start + length - 1) / 8)
  local value = tvb(first, last - first + 1):bitfield(start - first * 8, length)
  if signed then
    local half = 2 ^ (length - 1)
    if value >= half then
      value = value - 2 * half
    end
  end
  return value
end

-- Read `count` bytes at `first`, least significant byte first.
local function le(tvb, first, count, signed)
  local range = tvb(first, count)
  if signed then
    return range:le_int()
  end
  return range:le_uint()
end
'''


DISSECTOR = f'''
local function dissect(tvb, pinfo, tree)
  local identifier = can_id()
  if not identifier then
    return false
  end
  local message = messages[identifier.value]
  if message == nil then
    return false
  end
  -- Every identifier on this bus is extended. Declining a standard frame that
  -- happens to collide leaves it to whatever else wants it.
  local extended = can_extended()
  if message.extended and not (extended and extended.value) then
    return false
  end

  pinfo.cols.protocol = {lua_string(COLUMN_NAME)}
  pinfo.cols.info = message.name

  local subtree = tree:add({PROTOCOL}, tvb(), "Forest ebike: " .. message.name)
  subtree:add({MESSAGE_FIELD}, tvb(), message.name):set_generated()
  subtree:add(tvb(), message.comment)

  for _, signal in ipairs(message.signals) do
    -- A short payload is dissected as far as it goes rather than dropped: an
    -- unexpectedly truncated frame is itself worth seeing.
    if tvb:len() >= signal.first + signal.count then
      local item = subtree:add(
        signal.field,
        tvb(signal.first, signal.count),
        signal.value(tvb)
      )
      if signal.suffix ~= "" then
        item:append_text(signal.suffix)
      end
    end
  end

  return true
end

{PROTOCOL}:register_heuristic("can", dissect)
'''


def render(spec: dict) -> str:
    """Build the whole Lua file from the parsed TOML."""
    declarations: list[str] = []
    field_variables: list[str] = []
    entries: list[str] = []

    for message in spec.get("message", []):
        name = message["name"]
        confidence = message.get("confidence", "hypothesis")
        comment = f"[{confidence}] {summarise(message.get('comment', ''))}".strip()

        signal_entries = []
        for signal in message.get("signal", []):
            validate(message, signal)
            declarations.append(f"-- {name}.{signal['name']}: "
                                f"{' '.join(signal.get('evidence', '').split())[:96]}")
            declarations.append(field_declaration(message, signal))
            field_variables.append(variable_name(name, signal["name"]))
            first, count = byte_span(signal)
            signal_entries.append(
                "    {\n"
                f"      field = {variable_name(name, signal['name'])},\n"
                f"      first = {first},\n"
                f"      count = {count},\n"
                f"      suffix = {lua_string(suffix(signal))},\n"
                f"      value = function(tvb) return {value_expression(signal)} end,\n"
                "    },"
            )

        signals_block = "\n".join(signal_entries)
        if signals_block:
            signals_block = "\n" + signals_block + "\n  "
        entries.append(
            f"messages[{message['id']:#010x}] = {{\n"
            f"  name = {lua_prose(name)},\n"
            f"  extended = {'true' if message.get('extended', True) else 'false'},\n"
            f"  comment = {lua_prose(comment)},\n"
            f"  signals = {{{signals_block}}},\n"
            "}"
        )

    parts = [HEADER, "", "-- Signals. The evidence for each is in dbc/signals.toml."]
    parts.extend(declarations)
    parts.append("")
    parts.append(f"{PROTOCOL}.fields = {{")
    parts.append(f"  {MESSAGE_FIELD},")
    parts.extend(f"  {variable}," for variable in field_variables)
    parts.append("}")
    parts.append("")
    parts.append("local messages = {}")
    parts.append("")
    parts.append("\n\n".join(entries))
    parts.append(DISSECTOR)
    return "\n".join(parts)


def generate(spec_path: Path | None = None, out_path: Path | None = None) -> Path:
    spec = load_signals(spec_path or SIGNALS_TOML)
    out_path = out_path or OUTPUT_LUA
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(spec), encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Regenerate dbc/ebike.lua.")
    parser.add_argument("--signals", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    spec = load_signals(args.signals or SIGNALS_TOML)
    out = generate(args.signals, args.out)
    print(f"wrote {out}")
    for message in spec.get("message", []):
        signals = message.get("signal", [])
        print(f"  {int(message['id']):08X}  {message['name']:<24} {len(signals)} signals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
