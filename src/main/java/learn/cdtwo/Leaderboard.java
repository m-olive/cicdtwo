package learn.cdtwo;

import java.util.Comparator;
import java.util.List;
import java.util.Map;

public class Leaderboard {

    public static final int TOP_N = 3;
    public static final long MIN_VOTES = 5;

    private final UserRatings userRatings;

    public Leaderboard(UserRatings userRatings) {
        this.userRatings = userRatings;
    }

    public List<RankedGame> top(List<RatingRow> rows, String userId) {
        Map<String, Integer> mine = userRatings.forUser(userId);
        return rows.stream()
                .filter(row -> row.votes() > MIN_VOTES)
                .map(row -> new RankedGame(row.gameId(), row.title(), average(row), mine.get(row.gameId())))
                .sorted(Comparator.comparingDouble(RankedGame::average).reversed()
                        .thenComparing(RankedGame::title))
                .limit(TOP_N)
                .toList();
    }

    static double average(RatingRow row) {
        return (double) row.total() / row.votes();
    }
}
