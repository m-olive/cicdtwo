package learn.cdtwo;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class LeaderboardTest {

    static final List<RatingRow> ROWS = List.of(
            new RatingRow("g1", "Alpha", 10, 45),
            new RatingRow("g5", "Epsilon", 7, 28),
            new RatingRow("g2", "Beta", 5, 20),
            new RatingRow("g3", "Gamma", 4, 20),
            new RatingRow("g4", "Delta", 6, 21)
    );

    final Leaderboard leaderboard = new Leaderboard(new UserRatings(Map.of("u1", Map.of("g2", 3))));

    List<String> ids(List<RankedGame> games) {
        return games.stream().map(RankedGame::gameId).toList();
    }

    @Test
    void shouldRankTopGamesByAverage() {
        assertEquals(List.of("g1", "g2", "g5"), ids(leaderboard.top(ROWS, "u1")), "ranking");
    }

    @Test
    void shouldLimitToTopN() {
        assertEquals(3, leaderboard.top(ROWS, "u1").size(), "leaderboard size");
    }

    @Test
    void shouldIncludeGameAtVoteThreshold() {
        assertTrue(ids(leaderboard.top(ROWS, "u1")).contains("g2"), "game with exactly MIN_VOTES votes is ranked");
    }

    @Test
    void shouldExcludeGameBelowVoteThreshold() {
        assertFalse(ids(leaderboard.top(ROWS, "u1")).contains("g3"), "game below MIN_VOTES is not ranked");
    }

    @Test
    void shouldComputeFractionalAverage() {
        assertEquals(4.5, leaderboard.top(ROWS, "u1").get(0).average(), 1e-9, "average of 45 over 10 votes");
    }

    @Test
    void shouldBreakTiesByTitle() {
        List<String> titles = leaderboard.top(ROWS, "u1").stream().map(RankedGame::title).toList();
        assertEquals(List.of("Alpha", "Beta", "Epsilon"), titles, "equal averages ordered by title");
    }

    @Test
    void shouldCarryTitleWithGameId() {
        RankedGame first = leaderboard.top(ROWS, "u1").get(0);
        assertEquals("g1", first.gameId(), "game id");
        assertEquals("Alpha", first.title(), "title");
    }

    @Test
    void shouldAttachSignedInUserRating() {
        List<RankedGame> games = leaderboard.top(ROWS, "u1");
        assertEquals(3, games.get(1).yourRating(), "u1 rated g2");
        assertNull(games.get(0).yourRating(), "u1 did not rate g1");
    }

    @Test
    void shouldHandleSignedOutUser() {
        List<RankedGame> games = leaderboard.top(ROWS, null);
        assertTrue(games.stream().allMatch(g -> g.yourRating() == null), "signed-out user has no ratings");
    }
}
