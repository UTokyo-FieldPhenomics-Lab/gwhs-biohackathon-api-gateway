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
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import javax.imageio.ImageIO;

public final class Checks {

    private static final List<String> seen = new ArrayList<>();
    private static byte[] lastUpload;
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

            client.predict(
                    "sam3.1-meta",
                    "gwhs-temp",
                    7,
                    Map.of(
                            "prompt",
                            "wheat head",
                            "boxes",
                            List.of(List.of(480, 290, 110, 360, 1)),
                            "recursive",
                            true));
            String geometryCall = seen.get(seen.size() - 1);
            check(
                    "a geometry parameter is one JSON value",
                    geometryCall.contains("boxes=%5B%5B480%2C290%2C110%2C360%2C1%5D%5D"));
            check("a geometry parameter is not five names", !geometryCall.contains("boxes=480"));
            check("a boolean is true", geometryCall.contains("recursive=true"));

            Map<String, Object> uploaded =
                    client.predict_file("sam3.1-meta", new byte[] {(byte) 0xff, (byte) 0xd8}, "photo.jpg", null);
            check("an upload answers", uploaded.get("model").equals("sam3.1-meta"));
            check("an upload sends one file part", seen.get(seen.size() - 1).contains("name=\"file\""));
            check(
                    "bytes ImageIO cannot read go as they are",
                    Arrays.equals(uploadedImage(), new byte[] {(byte) 0xff, (byte) 0xd8}));

            byte[] portrait = sidewayPhoto();
            check("the fixture carries an orientation tag", GlobalWheat.exifOrientation(portrait) == 6);
            client.predict_file("sam3.1-meta", portrait, "photo.jpg", null);
            BufferedImage sent = decode(uploadedImage());
            check("an orientation-6 photo arrives upright", sent.getHeight() > sent.getWidth());
            check(
                    "at no more than the declared side",
                    Math.max(sent.getWidth(), sent.getHeight()) <= 1008);
            check("and smaller than the photograph", uploadedImage().length < portrait.length);

            int beforeOptOut = seen.size();
            client.predict_file("sam3.1-meta", portrait, "photo.jpg", null, false);
            check(
                    "resizeBeforeSubmit false sends the original length",
                    uploadedImage().length == portrait.length);
            check("and asks the model nothing first", seen.size() - beforeOptOut == 1);

            byte[] small = photo(800, 600, 0);
            client.predict_file("sam3.1-meta", small, "small.jpg", null);
            check("an image that already fits is unchanged", Arrays.equals(uploadedImage(), small));

            client.predict_file("gwfss-segformer-b4", portrait, "photo.jpg", null);
            BufferedImage segformer = decode(uploadedImage());
            check(
                    "a model that publishes 512 gets 512",
                    Math.max(segformer.getWidth(), segformer.getHeight()) == 512);

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

    /** A 4000x3000 photograph stored sideways, tagged orientation 6, as a portrait phone writes one. */
    private static byte[] sidewayPhoto() throws IOException {
        return photo(4000, 3000, 6);
    }

    /**
     * Build a JPEG with an EXIF orientation tag, written by hand because ImageIO cannot write one.
     *
     * @param width the stored width
     * @param height the stored height
     * @param orientation the tag 0x0112 value, or 0 for none
     * @return the file's bytes
     */
    private static byte[] photo(int width, int height, int orientation) throws IOException {
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < height; y += 16) {
            for (int x = 0; x < width; x += 16) {
                image.setRGB(x, y, (x * 255 / width) << 16 | (y * 255 / height) << 8 | 96);
            }
        }
        ByteArrayOutputStream encoded = new ByteArrayOutputStream();
        ImageIO.write(image, "jpeg", encoded);
        if (orientation == 0) {
            return encoded.toByteArray();
        }
        byte[] jpeg = encoded.toByteArray();
        byte[] segment = exifApp1(orientation);
        ByteArrayOutputStream tagged = new ByteArrayOutputStream();
        tagged.write(jpeg, 0, 2);                       // SOI, then the APP1, then the rest
        tagged.write(segment);
        tagged.write(jpeg, 2, jpeg.length - 2);
        return tagged.toByteArray();
    }

    /** One APP1 segment whose only TIFF entry is the orientation tag, big-endian. */
    private static byte[] exifApp1(int orientation) {
        byte[] tiff = {
            'M', 'M', 0, 42, 0, 0, 0, 8,               // big-endian, IFD0 at offset 8
            0, 1,                                       // one entry
            0x01, 0x12,                                 // tag 0x0112
            0, 3,                                       // type SHORT
            0, 0, 0, 1,                                 // count 1
            (byte) (orientation >> 8), (byte) orientation, 0, 0,
            0, 0, 0, 0                                  // no next IFD
        };
        int length = 2 + 6 + tiff.length;               // the two length bytes, "Exif\0\0", the TIFF
        ByteArrayOutputStream segment = new ByteArrayOutputStream();
        segment.write(0xff);
        segment.write(0xe1);
        segment.write(length >> 8);
        segment.write(length & 0xff);
        segment.writeBytes("Exif\0\0".getBytes(StandardCharsets.US_ASCII));
        segment.writeBytes(tiff);
        return segment.toByteArray();
    }

    /** The image part of the multipart body the handler recorded last. */
    private static byte[] uploadedImage() {
        byte[] header = "\r\n\r\n".getBytes(StandardCharsets.US_ASCII);
        int start = indexOf(lastUpload, header) + header.length;
        int end = lastUpload.length;
        for (int at = lastUpload.length - 4; at > start; at--) {
            if (lastUpload[at] == '\r'
                    && lastUpload[at + 1] == '\n'
                    && lastUpload[at + 2] == '-'
                    && lastUpload[at + 3] == '-') {
                end = at;
                break;
            }
        }
        return Arrays.copyOfRange(lastUpload, start, end);
    }

    private static int indexOf(byte[] haystack, byte[] needle) {
        for (int at = 0; at + needle.length <= haystack.length; at++) {
            boolean found = true;
            for (int offset = 0; offset < needle.length; offset++) {
                if (haystack[at + offset] != needle[offset]) {
                    found = false;
                    break;
                }
            }
            if (found) {
                return at;
            }
        }
        throw new IllegalStateException("the multipart body has no header separator");
    }

    private static BufferedImage decode(byte[] image) throws IOException {
        return ImageIO.read(new ByteArrayInputStream(image));
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
                    at.resolve("clients/fixtures/example-prediction.json");
            if (java.nio.file.Files.isRegularFile(candidate)) {
                return candidate;
            }
            at = at.getParent();
        }
        throw new IllegalStateException("cannot find clients/fixtures/example-prediction.json");
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
        byte[] raw = exchange.getRequestBody().readAllBytes();
        String body = new String(raw, StandardCharsets.UTF_8);
        seen.add(path + "?" + query + " " + body + " key=" + headers.get("key"));
        // The multipart body holds the upload as bytes; the recorded string has already mangled it.
        lastUpload = path.endsWith("/predict/upload") ? raw : null;

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
            case "/models/sam3.1-meta" ->
                    answer =
                            "{\"id\":\"sam3.1-meta\",\"resolution\":1008,\"declared\":true,\"parameters\":["
                                    + "{\"name\":\"imgsz\",\"type\":\"number\",\"values\":[672,784,896,1008]}]}";
            case "/models/gwfss-segformer-b4" ->
                    answer =
                            "{\"id\":\"gwfss-segformer-b4\",\"resolution\":512,\"declared\":true,\"parameters\":[]}";
            case "/models/sam3.1-meta/predict", "/models/sam3.1-meta/predict/upload" ->
                    answer =
                            "{\"model\":\"sam3.1-meta\",\"objects\":[{\"idx\":0,\"category\":\"head\","
                                    + "\"bbox\":[0,0,1,1],\"area\":2,\"rle\":{\"size\":[2,2],\"counts\":\"0120\"}}]}";
            case "/models/gwfss-segformer-b4/predict/upload" ->
                    answer = "{\"model\":\"gwfss-segformer-b4\",\"objects\":[]}";
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
