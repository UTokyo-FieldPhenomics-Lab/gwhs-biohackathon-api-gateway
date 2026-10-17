// The Java client's own checks: no dependency, and a real HTTP server rather than a stub.
//
// `com.sun.net.httpserver` is in the JDK, so this runs with
//
//   javac -d /tmp/checks ../GlobalWheat.java Checks.java && java -ea -cp /tmp/checks Checks
//
// which is the same shape a participant needs: one file, one javac, no build system. What it checks
// is the request the client builds and the way it reads an answer; the one check that needs the
// real API is the Python live test, which is the same claim written once.

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Checks {

    private static final List<String> seen = new ArrayList<>();
    private static int failures;

    public static void main(String[] args) throws Exception {
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", Checks::handle);
        server.start();
        String base = "http://127.0.0.1:" + server.getAddress().getPort();
        GlobalWheat.Client client = new GlobalWheat.Client("t0ken", base, 30);

        try {
            String first = (String) client.datasets().get(0).get("id");
            check("datasets", first.equals("gwhd"));

            List<Integer> rows = new ArrayList<>();
            for (Map<String, Object> row : client.iter_images("gwhd", 2, Map.of("split", "train"))) {
                rows.add(((Number) row.get("row")).intValue());
            }
            check("iter_images follows the cursor", rows.equals(List.of(1, 2, 3)));
            check("iter_images keeps the filter", seen.get(1).contains("split=train"));

            Map<String, Object> detail = client.image("gwhs-temp", 0);
            GlobalWheat.Mask mask = (GlobalWheat.Mask) objects(detail).get(0).get("mask");
            check("image decodes its mask", mask != null && mask.count() == 2);
            check("the raw rle is kept", objects(detail).get(0).get("rle") != null);

            Map<String, Object> plain = client.image("gwhs-temp", 0, false);
            check("decode false keeps the answer", objects(plain).get(0).get("mask") == null);

            Map<String, Object> prediction =
                    client.predict("sam3.1-meta", "gwhs-temp", 7, Map.of("prompt", "wheat head"));
            check("predict answers a body", prediction.get("model").equals("sam3.1-meta"));
            String predictCall = seen.get(seen.size() - 1);
            check("predict sends the address in the body", predictCall.contains("{\"dataset\":\"gwhs-temp\",\"row\":7}"));
            check("predict sends the parameters in the query", predictCall.contains("prompt=wheat+head"));
            check("the key travels", predictCall.contains("key=t0ken"));

            Map<String, Object> uploaded =
                    client.predict_file("sam3.1-meta", new byte[] {(byte) 0xff, (byte) 0xd8}, "photo.jpg", null);
            check("an upload answers", uploaded.get("model").equals("sam3.1-meta"));
            check("an upload sends one file part", seen.get(seen.size() - 1).contains("name=\"file\""));

            try {
                client.model("nope");
                check("a refusal raises", false);
            } catch (GlobalWheat.ApiError caught) {
                check("a refusal raises", caught.status == 401 && caught.code.equals("invalid_key"));
            }

            check("a recorded mask decodes to its area", recordedArea());
            check("a palette splits into one mask per class", palette());
            check("json round trips", json());
        } finally {
            server.stop(0);
        }

        if (failures > 0) {
            System.out.println(failures + " check(s) failed");
            System.exit(1);
        }
        System.out.println("all checks passed");
    }

    private static void check(String name, boolean passed) {
        if (!passed) {
            failures++;
            System.out.println("FAIL " + name);
        } else {
            System.out.println("ok   " + name);
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> objects(Map<String, Object> body) {
        return (List<Map<String, Object>>) body.get("objects");
    }

    /** Every recorded object's pixel count is the {@code area} beside it. */
    private static boolean recordedArea() throws IOException {
        String text =
                new String(
                        java.nio.file.Files.readAllBytes(example()),
                        StandardCharsets.UTF_8);
        Map<String, Object> document = (Map<String, Object>) GlobalWheat.Json.parse(text);
        Map<String, Object> response = (Map<String, Object>) document.get("response");
        for (Map<String, Object> object : (List<Map<String, Object>>) response.get("objects")) {
            Map<String, Object> rle = (Map<String, Object>) object.get("rle");
            List<Object> size = (List<Object>) rle.get("size");
            GlobalWheat.Mask mask =
                    GlobalWheat.rle_decode(
                            (String) rle.get("counts"),
                            ((Number) size.get(0)).intValue(),
                            ((Number) size.get(1)).intValue());
            if (mask.count() != ((Number) object.get("area")).intValue()) {
                return false;
            }
        }
        return true;
    }

    /** Find the recorded call by walking up from the working directory, so any cwd can run this. */
    private static java.nio.file.Path example() {
        java.nio.file.Path at = java.nio.file.Path.of("").toAbsolutePath();
        while (at != null) {
            java.nio.file.Path candidate =
                    at.resolve("docs/content/models/example-prediction.json");
            if (java.nio.file.Files.isRegularFile(candidate)) {
                return candidate;
            }
            at = at.getParent();
        }
        throw new IllegalStateException("cannot find docs/content/models/example-prediction.json");
    }

    private static boolean palette() {
        int green = 0x32ff84;
        int[] pixels = {green, 0xff000000};
        List<Map<String, Object>> classes = new ArrayList<>();
        classes.add(Map.of("name", "head", "color", "#32ff84"));
        classes.add(Map.of("name", "background", "color", "#000000"));
        Map<String, GlobalWheat.Mask> masks = GlobalWheat.label_masks(pixels, 2, 1, classes);
        return masks.get("head").count() == 1 && masks.get("background").count() == 1;
    }

    private static boolean json() {
        String text = "{\"a\":[1,2.5,\"x\",true,null],\"b\":{}}";
        String again = GlobalWheat.Json.write(GlobalWheat.Json.parse(text));
        return again.equals("{\"a\":[1,2.5,\"x\",true,null],\"b\":{}}");
    }

    /** One handler for every path the checks call, recording each request it was asked for. */
    @SuppressWarnings("unchecked")
    private static void handle(HttpExchange exchange) throws IOException {
        String path = exchange.getRequestURI().getPath();
        String query = exchange.getRequestURI().getRawQuery();
        Map<String, Object> headers = new LinkedHashMap<>();
        headers.put("key", exchange.getRequestHeaders().getFirst("X-API-Key"));
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        seen.add(path + "?" + query + " " + body + " key=" + headers.get("key"));

        String answer;
        int status = 200;
        switch (path) {
            case "/datasets" -> answer = "{\"datasets\":[{\"id\":\"gwhd\",\"rows\":10}]}";
            case "/datasets/gwhd/images" ->
                    answer =
                            query != null && query.contains("cursor=")
                                    ? "{\"dataset\":\"gwhd\",\"count\":1,\"next_cursor\":null,\"images\":[{\"row\":3}]}"
                                    : "{\"dataset\":\"gwhd\",\"count\":2,\"next_cursor\":\"c1\",\"images\":[{\"row\":1},{\"row\":2}]}";
            case "/datasets/gwhs-temp/images/0" ->
                    answer =
                            "{\"dataset\":\"gwhs-temp\",\"row\":0,\"objects\":[{\"idx\":0,\"category\":\"head\","
                                    + "\"bbox\":[0,0,1,1],\"area\":2,\"rle\":{\"size\":[2,2],\"counts\":\"0120\"}}]}";
            case "/models/sam3.1-meta/predict", "/models/sam3.1-meta/predict/upload" ->
                    answer =
                            "{\"model\":\"sam3.1-meta\",\"objects\":[{\"idx\":0,\"category\":\"head\","
                                    + "\"bbox\":[0,0,1,1],\"area\":2,\"rle\":{\"size\":[2,2],\"counts\":\"0120\"}}]}";
            default -> {
                status = 401;
                answer = "{\"error\":{\"code\":\"invalid_key\",\"message\":\"that key is not in the pool\"}}";
            }
        }
        byte[] bytes = answer.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (InputStream ignored = null; var out = exchange.getResponseBody()) {
            out.write(bytes);
        }
    }
}
