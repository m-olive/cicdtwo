package learn.cdtwo;

import java.util.Map;

public class UserRatings {

    private final Map<String, Map<String, Integer>> byUser;

    public UserRatings(Map<String, Map<String, Integer>> byUser) {
        this.byUser = byUser;
    }

    public Map<String, Integer> forUser(String userId) {
        if (userId == null) {
            return Map.of();
        }
        return byUser.getOrDefault(userId, Map.of());
    }
}
