package learn.cdtwo;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;

class SteamIdParserTest {

    static final String ID = "76561198253773512";

    @Test
    void shouldParseBareId() {
        assertEquals(Optional.of(ID), SteamIdParser.parse(ID), "bare id");
    }

    @Test
    void shouldParseProfileUrl() {
        assertEquals(Optional.of(ID), SteamIdParser.parse("https://steamcommunity.com/profiles/" + ID), "profile url");
    }

    @Test
    void shouldParseProfileUrlWithTrailingPath() {
        assertEquals(Optional.of(ID), SteamIdParser.parse("steamcommunity.com/profiles/" + ID + "/games/?tab=all"), "trailing path");
    }

    @Test
    void shouldReturnEmptyForNull() {
        assertTrue(SteamIdParser.parse(null).isEmpty(), "null input");
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "   ", "7656119825377351", "86561198253773512", "https://example.com/profiles/76561198253773512"})
    void shouldRejectInvalidInput(String raw) {
        assertTrue(SteamIdParser.parse(raw).isEmpty(), "input: " + raw);
    }
}
