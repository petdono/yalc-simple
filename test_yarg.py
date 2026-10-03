import struct
import unittest

from yarg import (
    LightingIntent,
    describe_changes,
    intent_for_state,
    parse_packet,
)


def packet_v3(section: int = 0, cue: int = 0, strobe: int = 24) -> bytes:
    data = bytearray(47)
    struct.pack_into("<IB", data, 0, 0x59415247, 3)
    data[13] = section
    data[34] = cue
    data[37] = strobe
    return bytes(data)


def packet_v5(section: int = 0, cue: int = 0, strobe: int = 24) -> bytes:
    data = bytearray(51)
    struct.pack_into("<IB", data, 0, 0x59415247, 5)
    data[13] = section
    data[34] = cue
    data[39] = strobe
    return bytes(data)


def packet_v4(section: int = 0, cue: int = 0, strobe: int = 24) -> bytes:
    data = bytearray(49)
    struct.pack_into("<IB", data, 0, 0x59415247, 4)
    data[13] = section
    data[34] = cue
    data[37] = strobe
    return bytes(data)


class YargPacketTests(unittest.TestCase):
    def test_reads_current_yarg_v3_layout(self) -> None:
        state = parse_packet(packet_v3(section=5, cue=11, strobe=20))
        self.assertEqual(state.version, 3)
        self.assertEqual((state.section, state.cue, state.strobe), (5, 11, 20))

    def test_reads_v5_strobe_after_fog_duration(self) -> None:
        state = parse_packet(packet_v5(section=2, cue=8, strobe=22))
        self.assertEqual((state.section, state.cue, state.strobe), (2, 8, 22))

    def test_reads_v4_trailing_player_count(self) -> None:
        state = parse_packet(packet_v4(section=2, strobe=23))
        self.assertEqual((state.section, state.strobe), (2, 23))

    def test_rejects_malformed_header_and_short_packets(self) -> None:
        with self.assertRaisesRegex(ValueError, "header"):
            parse_packet(b"nope!")
        with self.assertRaisesRegex(ValueError, "too short"):
            parse_packet(b"")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            parse_packet(packet_v3()[:-1])

    def test_blackout_and_strobe_translate_to_states(self) -> None:
        blackout = intent_for_state(parse_packet(packet_v3(cue=8)))
        strobe = intent_for_state(parse_packet(packet_v3(strobe=21)))
        self.assertTrue(blackout.blackout)
        self.assertEqual(strobe.strobe_interval, 0.16)

    def test_strobe_off_state_stops_a_stale_strobe_cue(self) -> None:
        intent = intent_for_state(parse_packet(packet_v3(cue=20, strobe=24)))
        self.assertIsNone(intent.strobe_interval)

    def test_sections_log_their_practical_color_mapping(self) -> None:
        previous = parse_packet(packet_v3())
        current = parse_packet(packet_v3(section=5))
        self.assertEqual(
            describe_changes(previous, current), ["YARG: VERSE -> BLUE"]
        )
        self.assertEqual(
            intent_for_state(current), LightingIntent("BLUE", transition_ms=120)
        )


if __name__ == "__main__":
    unittest.main()
