"""Parser and small cue-to-lighting translation for YARG's UDP datastream."""

from __future__ import annotations

from dataclasses import dataclass

PACKET_HEADER = 0x59415247
LEGACY_PACKET_SIZE = 47
V4_FIXED_PACKET_SIZE = 49
V5_FIXED_PACKET_SIZE = 51

SECTION_NAMES = {0: "NONE", 2: "CHORUS", 5: "VERSE"}

CUE_NAMES = {
    0: "DEFAULT",
    1: "DISCHORD",
    2: "CHORUS",
    3: "COOL_MANUAL",
    4: "STOMP",
    5: "VERSE",
    6: "WARM_MANUAL",
    7: "BIG_ROCK_ENDING",
    8: "BLACKOUT_FAST",
    9: "BLACKOUT_SLOW",
    10: "BLACKOUT_SPOTLIGHT",
    11: "COOL_AUTOMATIC",
    12: "FLARE_FAST",
    13: "FLARE_SLOW",
    14: "FRENZY",
    15: "INTRO",
    16: "HARMONY",
    17: "SILHOUETTES",
    18: "SILHOUETTES_SPOTLIGHT",
    19: "SEARCHLIGHTS",
    20: "STROBE_FASTEST",
    21: "STROBE_FAST",
    22: "STROBE_MEDIUM",
    23: "STROBE_SLOW",
    24: "STROBE_OFF",
    25: "SWEEP",
    26: "WARM_AUTOMATIC",
    27: "KEYFRAME_FIRST",
    28: "KEYFRAME_NEXT",
    29: "KEYFRAME_PREVIOUS",
    30: "MENU",
    31: "SCORE",
    32: "NO_CUE",
}

STROBE_INTERVALS = {
    20: 0.10,
    21: 0.16,
    22: 0.26,
    23: 0.42,
}

CUE_COLORS = {
    1: "PURPLE",
    2: "YELLOW",
    3: "BLUE",
    4: "WHITE",
    5: "BLUE",
    6: "ORANGE",
    7: "WHITE",
    11: "BLUE",
    12: "YELLOW",
    13: "YELLOW",
    14: "PURPLE",
    15: "WHITE",
    16: "GREEN",
    17: "PURPLE",
    18: "PURPLE",
    19: "WHITE",
    25: "CYAN",
    26: "ORANGE",
    30: "WHITE",
    31: "WHITE",
}

BLACKOUT_CUES = {8, 9, 10}


@dataclass(frozen=True)
class YargState:
    version: int
    section: int
    cue: int
    strobe: int


@dataclass(frozen=True)
class LightingIntent:
    color: str
    blackout: bool = False
    strobe_interval: float | None = None
    transition_ms: int = 120


def parse_packet(packet: bytes) -> YargState:
    """Parse only the YARG datastream fields needed to drive lights."""
    if len(packet) < 5:
        raise ValueError(f"packet too short for YARG header ({len(packet)} bytes)")
    if int.from_bytes(packet[:4], "little") != PACKET_HEADER:
        raise ValueError("packet header is not YARG")

    version = packet[4]
    if version >= 5:
        fixed_size = V5_FIXED_PACKET_SIZE
        count_offset = 49
    elif version == 4:
        fixed_size = V4_FIXED_PACKET_SIZE
        count_offset = 47
    else:
        fixed_size = LEGACY_PACKET_SIZE
        count_offset = None

    if len(packet) < fixed_size:
        raise ValueError(
            f"version {version} packet is incomplete "
            f"({len(packet)} bytes; expected at least {fixed_size})"
        )

    if count_offset is None:
        expected_size = fixed_size
    else:
        player_count = int.from_bytes(packet[count_offset : count_offset + 2], "little")
        expected_size = fixed_size + player_count * 2
    if len(packet) != expected_size:
        raise ValueError(
            f"version {version} packet has {len(packet)} bytes; expected {expected_size}"
        )

    strobe_offset = 39 if version >= 5 else 37
    return YargState(
        version=version,
        section=packet[13],
        cue=packet[34],
        strobe=packet[strobe_offset],
    )


def _section_color(section: int) -> str:
    if section == 2:
        return "YELLOW"
    if section == 5:
        return "BLUE"
    return "WHITE"


def color_for_state(state: YargState) -> str:
    if state.cue in CUE_COLORS:
        return CUE_COLORS[state.cue]
    return _section_color(state.section)


def intent_for_state(
    state: YargState, transition_ms: int = 120
) -> LightingIntent:
    if state.cue in BLACKOUT_CUES:
        return LightingIntent("WHITE", blackout=True, transition_ms=0)

    strobe_interval = STROBE_INTERVALS.get(state.strobe)
    if strobe_interval is None and state.strobe != 24:
        strobe_interval = STROBE_INTERVALS.get(state.cue)
    return LightingIntent(
        color=color_for_state(state),
        strobe_interval=strobe_interval,
        transition_ms=transition_ms,
    )


def describe_changes(previous: YargState | None, current: YargState) -> list[str]:
    """Return concise log lines only for changes relevant to lighting."""
    if previous is None:
        previous = YargState(current.version, -1, -1, -1)

    lines: list[str] = []
    if current.section != previous.section and current.section in (2, 5):
        name = SECTION_NAMES[current.section]
        lines.append(f"YARG: {name} -> {_section_color(current.section)}")

    if current.cue != previous.cue:
        if current.cue in BLACKOUT_CUES:
            lines.append("YARG: BLACKOUT")
        elif current.cue in STROBE_INTERVALS:
            lines.append(f"YARG: STROBE ({CUE_NAMES[current.cue].removeprefix('STROBE_')})")
        elif current.cue in CUE_COLORS:
            lines.append(
                f"YARG: {CUE_NAMES[current.cue]} -> {CUE_COLORS[current.cue]}"
            )

    if current.strobe != previous.strobe and current.strobe not in STROBE_INTERVALS:
        if current.strobe == 24 and previous.strobe in STROBE_INTERVALS:
            lines.append("YARG: STROBE OFF")
    elif current.strobe != previous.strobe and current.strobe in STROBE_INTERVALS:
        if current.cue not in STROBE_INTERVALS:
            name = CUE_NAMES[current.strobe].removeprefix("STROBE_")
            lines.append(f"YARG: STROBE ({name})")

    return lines
