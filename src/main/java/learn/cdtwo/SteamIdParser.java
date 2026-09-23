package learn.cdtwo;

import java.util.Optional;
import java.util.regex.Pattern;

public class SteamIdParser {

    private static final Pattern STEAM_ID_64 = Pattern.compile("7656\\d{13}");
    private static final String PROFILE_PREFIX = "steamcommunity.com/profiles/";

    public static Optional<String> parse(String raw) {
        if (raw == null) {
            return Optional.empty();
        }

        String value = raw.trim().toLowerCase();
        value = stripScheme(value);

        if (value.startsWith(PROFILE_PREFIX)) {
            value = value.substring(PROFILE_PREFIX.length());
            int slash = value.indexOf('/');
            if (slash >= 0) {
                value = value.substring(0, slash);
            }
        }

        if (STEAM_ID_64.matcher(value).matches()) {
            return Optional.of(value);
        }
        return Optional.empty();
    }

    private static String stripScheme(String value) {
        for (String prefix : new String[]{"https://www.", "http://www.", "https://", "http://", "www."}) {
            if (value.startsWith(prefix)) {
                return value.substring(prefix.length());
            }
        }
        return value;
    }
}
