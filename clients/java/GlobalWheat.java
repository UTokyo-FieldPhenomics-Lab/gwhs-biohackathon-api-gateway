// Global Wheat API client: one file, then datasets, models, and masks.
//
// The API is eight HTTP routes; this file is those eight routes as methods, plus the two decoders a
// result needs before it is usable. It carries **no response models**: every answer is the API's own
// JSON, parsed into a `Map`/`List` tree, so this file cannot disagree with the server about a field
// it does not restate. Only the routes are named here, and `clients/routes.json` beside this file is
// what keeps them honest against the gateway's OpenAPI document.
//
// Three things are done for the caller rather than by the caller:
//
//   * an address, not a URL — `image("gwhd", 0)` resolves `(dataset, row)` for you;
//   * the cursor — `iter_images` walks every page so a loop does not have to;
//   * the geometry — every object comes back with `mask` already decoded, so nothing has to know
//     what COCO's compressed RLE is.
//
// It is one file with no dependencies: `HttpURLConnection` for transport and a small JSON reader
// here, because a client that needs a build file is a client a participant has to integrate before
// their first call. That is also what makes it work unchanged on Android — declare the `INTERNET`
// permission and call it off the main thread, which is where the network belongs anyway:
//
//   GlobalWheat.Client client = new GlobalWheat.Client();      // key from GLOBAL_WHEAT_API_KEY
//   Map<String, Object> row = client.image("gwhd", 0);
//   for (Object object : (List<Object>) row.get("objects")) {
//       GlobalWheat.Mask mask = (GlobalWheat.Mask) ((Map<?, ?>) object).get("mask");
//       System.out.println(mask.width() + "x" + mask.height());
//   }

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.NoSuchElementException;

public final class GlobalWheat {

    public static final String DEFAULT_BASE = "https://api.global-wheat.com";
    /** The public gateway. Overridden by the constructor or by {@code GLOBAL_WHEAT_API}. */
    public static final String BASE_ENV = "GLOBAL_WHEAT_API";
    /** The environment variable a host override is read from, so a run needs no argument. */
    public static final String KEY_ENV = "GLOBAL_WHEAT_API_KEY";
    /** The environment variable the participant token is read from. */
    public static final String API_KEY_HEADER = "X-API-Key";
    /** Where the token travels. It is sent to the API's own origin and to nothing else. */
    public static final double DEFAULT_TIMEOUT_SECONDS = 60.0;
    /** Seconds to wait for an answer. A predict route gives up on a backend long before this. */
    public static final int DEFAULT_PAGE_SIZE = 100;
    /** Rows per page. The API's own default, and its ceiling is 1000. */

    private GlobalWheat() {
        // The client is a nested class; this type is the file it lives in.
    }

    /** One refusal, in the API's own terms. */
    public static final class ApiError extends RuntimeException {
        private static final long serialVersionUID = 1L;

        /** The HTTP status the API answered with. */
        public final int status;
        /** The machine-readable code from the error body, or one chosen by the status's class. */
        public final String code;
        /** The human-readable sentence the API wrote. It is safe to print. */
        public final String message;

        ApiError(int status, String code, String message) {
            super(status + " " + code + ": " + message);
            this.status = status;
            this.code = code;
            this.message = message;
        }
    }

    /** One decoded mask: row-major pixels, so {@code data[y * width + x]} is the pixel at (x, y). */
    public static final class Mask {
        /** The mask's width in pixels. */
        public final int width;
        /** The mask's height in pixels. */
        public final int height;
        /** The pixels, row-major; {@code true} is the object. */
        public final boolean[] data;

        Mask(int width, int height, boolean[] data) {
            this.width = width;
            this.height = height;
            this.data = data;
        }

        /** How many pixels the mask covers, which is the {@code area} beside it in an answer. */
        public int count() {
            int total = 0;
            for (boolean pixel : data) {
                if (pixel) {
                    total++;
                }
            }
            return total;
        }
    }

    /** One participant's connection to the API. */
    public static final class Client {
        private final String key;
        private final String base;
        private final double timeoutSeconds;
        private final Map<String, Map<String, Object>> descriptions = new LinkedHashMap<>();

        /**
         * Build a client from the environment: {@code GLOBAL_WHEAT_API_KEY} and, where it is set,
         * {@code GLOBAL_WHEAT_API}.
         *
         * @throws IllegalStateException where no key is in the environment
         */
        public Client() {
            this(System.getenv(KEY_ENV), System.getenv(BASE_ENV), DEFAULT_TIMEOUT_SECONDS);
        }

        /**
         * Build a client for one token, against the public gateway.
         *
         * @param key the participant token
         */
        public Client(String key) {
            this(key, null, DEFAULT_TIMEOUT_SECONDS);
        }

        /**
         * Build a client.
         *
         * @param key the participant token; {@code null} reads the environment
         * @param base the API's origin; {@code null} reads the environment, then the public host
         * @param timeoutSeconds seconds to wait for an answer
         * @throws IllegalStateException where no key can be found
         */
        public Client(String key, String base, double timeoutSeconds) {
            String found = key != null ? key : System.getenv(KEY_ENV);
            if (found == null || found.isEmpty()) {
                throw new IllegalStateException(
                        "no API key: pass one or set " + KEY_ENV + " in the environment");
            }
            String origin = base != null ? base : System.getenv(BASE_ENV);
            if (origin == null || origin.isEmpty()) {
                origin = DEFAULT_BASE;
            }
            this.key = found;
            this.base = origin.replaceAll("/+$", "");
            this.timeoutSeconds = timeoutSeconds;
        }

        // -- the dataset surface ---------------------------------------------

        /**
         * List the datasets this API serves.
         *
         * @return one entry per dataset: its id, row count, splits, and the shape of its annotation
         */
        public List<Map<String, Object>> datasets() {
            return array(call("GET", "/datasets", null, null, null).get("datasets"));
        }

        /**
         * Describe one dataset: provenance, splits, palette, and filter keys. Remembered for the
         * life of the client, because the palette is what {@code label_masks} needs every call.
         *
         * @param dataset the dataset id
         * @return the description, with {@code classes} where the dataset has a palette
         */
        public Map<String, Object> describe(String dataset) {
            return descriptions.computeIfAbsent(
                    dataset, id -> call("GET", "/datasets/" + segment(id), null, null, null));
        }

        /**
         * Return one page of rows a filter selects.
         *
         * @param dataset the dataset id
         * @param limit the page's ceiling, at most 1000
         * @param cursor the position a previous page handed on, or {@code null} for the first
         * @param filters filter pairs such as {@code split=train}; repeating a key narrows the
         *     result rather than widening it
         * @return {@code dataset}, {@code count}, {@code next_cursor}, and {@code images}
         */
        public Map<String, Object> images(
                String dataset, int limit, String cursor, Map<String, ?> filters) {
            Map<String, Object> query = new LinkedHashMap<>();
            query.put("limit", limit);
            if (filters != null) {
                query.putAll(filters);
            }
            if (cursor != null && !cursor.isEmpty()) {
                query.put("cursor", cursor);
            }
            return call("GET", "/datasets/" + segment(dataset) + "/images", query, null, null);
        }

        /**
         * Walk every row a filter selects, page by page.
         *
         * @param dataset the dataset id
         * @param limit rows per request, which is all this controls
         * @param filters the same filter pairs {@code images} takes
         * @return an iterable of row summaries, fetched one page at a time
         */
        public Iterable<Map<String, Object>> iter_images(
                String dataset, int limit, Map<String, ?> filters) {
            return () ->
                    new Iterator<Map<String, Object>>() {
                        private final List<Map<String, Object>> buffer = new ArrayList<>();
                        private String cursor;
                        private boolean exhausted;

                        @Override
                        public boolean hasNext() {
                            fill();
                            return !buffer.isEmpty();
                        }

                        @Override
                        public Map<String, Object> next() {
                            fill();
                            if (buffer.isEmpty()) {
                                throw new NoSuchElementException();
                            }
                            return buffer.remove(0);
                        }

                        private void fill() {
                            if (!buffer.isEmpty() || exhausted) {
                                return;
                            }
                            Map<String, Object> page = images(dataset, limit, cursor, filters);
                            buffer.addAll(array(page.get("images")));
                            //noinspection unchecked
                            cursor = (String) page.get("next_cursor");
                            exhausted = cursor == null || cursor.isEmpty();
                        }
                    };
        }

        /** Return one row's whole annotation, with each object's {@code mask} decoded. */
        public Map<String, Object> image(String dataset, int row) {
            return image(dataset, row, true);
        }

        /**
         * Return one row's whole annotation.
         *
         * @param dataset the dataset id
         * @param row the absolute row of the pinned upstream revision
         * @param decode attach each object's {@code mask}, decoded
         * @return the row, its metadata, and its objects where the dataset carries geometry
         */
        public Map<String, Object> image(String dataset, int row, boolean decode) {
            Map<String, Object> body =
                    call("GET", "/datasets/" + segment(dataset) + "/images/" + row, null, null, null);
            if (decode) {
                decodeObjects(body);
            }
            return body;
        }

        /** The image itself, fetched from the public {@code data.*} plane. */
        public byte[] image_bytes(String dataset, int row) {
            //noinspection unchecked
            return fetch((String) image(dataset, row, false).get("image_url"));
        }

        /** The row's mask image, where the dataset ships one. */
        public byte[] mask_bytes(String dataset, int row) {
            Object url = image(dataset, row, false).get("mask_url");
            if (url == null) {
                throw new IllegalArgumentException(dataset + " row " + row + " publishes no mask");
            }
            return fetch((String) url);
        }

        /**
         * Fetch a public object by the URL a response carried. Deliberately a plain request with no
         * key: {@code data.*} is a public distribution, and sending the participant token to a
         * second host is how a token leaks into a CDN's logs.
         */
        public byte[] fetch(String url) {
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) java.net.URI.create(url).toURL().openConnection();
                connection.setConnectTimeout((int) (timeoutSeconds * 1000));
                connection.setReadTimeout((int) (timeoutSeconds * 1000));
                connection.setRequestProperty("Accept-Encoding", "identity");
                int status = connection.getResponseCode();
                if (status >= 400) {
                    throw new ApiError(status, "not_found", "no object at " + url);
                }
                try (InputStream body = connection.getInputStream()) {
                    return read(body);
                }
            } catch (IOException failure) {
                throw new ApiError(0, "unavailable", failure.getMessage());
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }
        }

        // -- the model surface -----------------------------------------------

        /** List the models this API serves. */
        public List<Map<String, Object>> models() {
            return array(call("GET", "/models", null, null, null).get("models"));
        }

        /** Describe one model, freshly: its side, its machines, and its parameters. */
        public Map<String, Object> model(String model) {
            return call("GET", "/models/" + segment(model), null, null, null);
        }

        /** Run one model on one published row, with the model's own default parameters. */
        public Map<String, Object> predict(String model, String dataset, int row) {
            return predict(model, dataset, row, null);
        }

        /**
         * Run one model on one published row.
         *
         * @param model the model id
         * @param dataset the dataset id
         * @param row the absolute row; the gateway resolves the address, so no URL is ever sent
         * @param params the model's own parameters, forwarded verbatim under their own names
         * @return {@code model}, {@code objects}, {@code resolution_used}, {@code computed_by}
         */
        public Map<String, Object> predict(
                String model, String dataset, int row, Map<String, ?> params) {
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("dataset", dataset);
            body.put("row", row);
            Map<String, Object> answer =
                    call("POST", "/models/" + segment(model) + "/predict", params, body, null);
            decodeObjects(answer);
            return answer;
        }

        /** Run one model on your own photograph, read from a file. */
        public Map<String, Object> predict_file(String model, File image, Map<String, ?> params) {
            try (InputStream stream = new java.io.FileInputStream(image)) {
                return predict_file(model, read(stream), image.getName(), params);
            } catch (IOException failure) {
                throw new IllegalArgumentException("cannot read " + image, failure);
            }
        }

        /**
         * Run one model on your own photograph.
         *
         * @param model the model id
         * @param image the bytes: JPEG, PNG, WebP, TIFF or GIF, at most 15 MB; the API decides the
         *     type from the bytes rather than from a declared one
         * @param filename the name multipart requires, never trusted by the API
         * @param params the model's own parameters
         * @return the same body {@code predict} answers
         */
        public Map<String, Object> predict_file(
                String model, byte[] image, String filename, Map<String, ?> params) {
            Map<String, Object> answer =
                    call("POST", "/models/" + segment(model) + "/predict/upload", params, null, image);
            decodeObjects(answer);
            return answer;
        }

        // -- geometry ---------------------------------------------------------

        /**
         * Split a palette mask into one mask per class, using the dataset's own palette.
         *
         * @param pixels pixels as a canvas or an image gives them back: ARGB, or RGB where the image
         *     has no alpha channel
         * @param width the image's width
         * @param height the image's height
         * @param dataset the dataset whose palette applies
         * @return one mask per class, in palette order
         */
        public Map<String, Mask> label_masks(int[] pixels, int width, int height, String dataset) {
            List<Map<String, Object>> classes = array(describe(dataset).get("classes"));
            if (classes.isEmpty()) {
                throw new IllegalArgumentException(dataset + " publishes no palette");
            }
            return GlobalWheat.label_masks(pixels, width, height, classes);
        }

        // -- transport --------------------------------------------------------

        private Map<String, Object> call(
                String method,
                String path,
                Map<String, ?> query,
                Map<String, ?> json,
                byte[] upload) {
            HttpURLConnection connection = null;
            try {
                connection =
                        (HttpURLConnection)
                                java.net.URI.create(base + path + queryString(query))
                                        .toURL()
                                        .openConnection();
                connection.setRequestMethod(method);
                connection.setConnectTimeout((int) (timeoutSeconds * 1000));
                connection.setReadTimeout((int) (timeoutSeconds * 1000));
                connection.setRequestProperty(API_KEY_HEADER, key);
                connection.setRequestProperty("Accept", "application/json");
                connection.setRequestProperty("Accept-Encoding", "identity");

                if (json != null) {
                    byte[] body = Json.write(json).getBytes(StandardCharsets.UTF_8);
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", "application/json");
                    connection.setFixedLengthStreamingMode(body.length);
                    try (OutputStream out = connection.getOutputStream()) {
                        out.write(body);
                    }
                } else if (upload != null) {
                    byte[] body = multipart(upload);
                    connection.setDoOutput(true);
                    connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + BOUNDARY);
                    connection.setFixedLengthStreamingMode(body.length);
                    try (OutputStream out = connection.getOutputStream()) {
                        out.write(body);
                    }
                }

                int status = connection.getResponseCode();
                InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
                String text = stream == null ? "" : new String(read(stream), StandardCharsets.UTF_8);
                if (status >= 400) {
                    throw refusal(status, text);
                }
                return object(Json.parse(text));
            } catch (IOException failure) {
                throw new ApiError(0, "unavailable", String.valueOf(failure.getMessage()));
            } finally {
                if (connection != null) {
                    connection.disconnect();
                }
            }
        }

        /** Read a refusal in the contract's one error shape, falling back by status class. */
        private static ApiError refusal(int status, String text) {
            String code = status >= 500 ? "internal_error" : "invalid_request";
            String message = text.trim();
            try {
                Object error = object(Json.parse(text)).get("error");
                if (error instanceof Map<?, ?> detail) {
                    if (detail.get("code") instanceof String found) {
                        code = found;
                    }
                    if (detail.get("message") instanceof String found) {
                        message = found;
                    }
                }
            } catch (RuntimeException ignored) {
                // A proxy's HTML page is not a reason to lose the status.
            }
            return new ApiError(status, code, message);
        }

        private static final String BOUNDARY = "----GlobalWheatBoundary";

        /** Build the one multipart part the upload route takes. */
        private static byte[] multipart(byte[] image) throws IOException {
            ByteArrayOutputStream body = new ByteArrayOutputStream();
            body.write(
                    ("--" + BOUNDARY + "\r\n"
                                    + "Content-Disposition: form-data; name=\"file\"; filename=\"upload.jpg\"\r\n"
                                    + "Content-Type: application/octet-stream\r\n\r\n")
                            .getBytes(StandardCharsets.UTF_8));
            body.write(image);
            body.write(("\r\n--" + BOUNDARY + "--\r\n").getBytes(StandardCharsets.UTF_8));
            return body.toByteArray();
        }

        /** Encode query pairs, repeating a key for every value of a list. */
        private static String queryString(Map<String, ?> query) {
            if (query == null || query.isEmpty()) {
                return "";
            }
            StringBuilder text = new StringBuilder("?");
            for (Map.Entry<String, ?> entry : query.entrySet()) {
                Object value = entry.getValue();
                List<?> values =
                        value instanceof List<?> list
                                ? list
                                : java.util.Collections.singletonList(value);
                for (Object item : values) {
                    if (item == null) {
                        continue;
                    }
                    if (text.length() > 1) {
                        text.append('&');
                    }
                    text.append(URLEncoder.encode(entry.getKey(), StandardCharsets.UTF_8))
                            .append('=')
                            .append(URLEncoder.encode(String.valueOf(item), StandardCharsets.UTF_8));
                }
            }
            return text.length() > 1 ? text.toString() : "";
        }

        private static byte[] read(InputStream stream) throws IOException {
            ByteArrayOutputStream collected = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int count;
            while ((count = stream.read(buffer)) > 0) {
                collected.write(buffer, 0, count);
            }
            return collected.toByteArray();
        }
    }

    /**
     * Decode a COCO compressed RLE into the mask it describes.
     *
     * @param counts the {@code rle.counts} string, verbatim
     * @param height the mask's height, as {@code rle.size} publishes it
     * @param width the mask's width
     * @return the mask, row-major
     */
    public static Mask rle_decode(String counts, int height, int width) {
        List<Integer> runs = new ArrayList<>();
        int value = 0;
        int shift = 0;
        for (int i = 0; i < counts.length(); i++) {
            int chunk = counts.charAt(i) - 48;
            value |= (chunk & 0x1f) << shift;
            shift += 5;
            if ((chunk & 0x20) != 0) {
                continue;
            }
            if ((chunk & 0x10) != 0) {
                value |= -1 << shift;   // the last chunk of a negative number is sign-extended
            }
            runs.add(value + (runs.size() > 2 ? runs.get(runs.size() - 2) : 0));
            value = 0;
            shift = 0;
        }

        int pixels = height * width;
        long total = 0;
        for (int run : runs) {
            total += run;
        }
        if (total != pixels) {
            throw new IllegalArgumentException(
                    "the counts describe " + total + " pixels, not " + pixels);
        }

        // Column-major first, because that is the order the runs are written in, then one transpose.
        boolean[] column = new boolean[pixels];
        int position = 0;
        for (int index = 0; index < runs.size(); index++) {
            if (index % 2 == 1) {
                java.util.Arrays.fill(column, position, position + runs.get(index), true);
            }
            position += runs.get(index);
        }
        boolean[] data = new boolean[pixels];
        for (int x = 0; x < width; x++) {
            for (int y = 0; y < height; y++) {
                data[y * width + x] = column[x * height + y];
            }
        }
        return new Mask(width, height, data);
    }

    /**
     * Split a palette mask into one mask per class.
     *
     * @param pixels ARGB pixels, or RGB where the image has no alpha channel
     * @param width the image's width
     * @param height the image's height
     * @param classes palette entries with {@code name} and {@code color} ({@code #rrggbb}), exactly
     *     as a dataset's description publishes its {@code classes}
     * @return one mask per class, in palette order
     */
    public static Map<String, Mask> label_masks(
            int[] pixels, int width, int height, List<Map<String, Object>> classes) {
        Map<String, Mask> masks = new LinkedHashMap<>();
        boolean[] known = new boolean[width * height];
        for (Map<String, Object> entry : classes) {
            int packed = pack(String.valueOf(entry.get("color")));
            boolean[] data = new boolean[width * height];
            for (int i = 0; i < data.length; i++) {
                if ((pixels[i] & 0xffffff) == packed) {
                    data[i] = true;
                    known[i] = true;
                }
            }
            masks.put(String.valueOf(entry.get("name")), new Mask(width, height, data));
        }
        for (boolean pixel : known) {
            if (!pixel) {
                throw new IllegalArgumentException(
                        "the mask holds a colour that is not in the palette");
            }
        }
        return masks;
    }

    /** Pack one {@code #rrggbb} colour into an integer, so a pixel can be compared with {@code ==}. */
    private static int pack(String colour) {
        String text = colour.startsWith("#") ? colour.substring(1) : colour;
        if (text.length() != 6) {
            throw new IllegalArgumentException("a colour must be #rrggbb, got " + colour);
        }
        return Integer.parseInt(text, 16);
    }

    /** Attach each object's decoded {@code mask}, in place. */
    @SuppressWarnings("unchecked")
    private static void decodeObjects(Map<String, Object> body) {
        Object objects = body.get("objects");
        if (!(objects instanceof List<?> list)) {
            return;
        }
        for (Object item : list) {
            if (item instanceof Map<?, ?>) {
                Map<String, Object> object = (Map<String, Object>) item;
                if (object.get("rle") instanceof Map<?, ?> rle) {
                    List<Object> size = items(rle.get("size"));
                    int height = ((Number) size.get(0)).intValue();
                    int width = ((Number) size.get(1)).intValue();
                    object.put("mask", rle_decode((String) rle.get("counts"), height, width));
                }
            }
        }
    }

    /** Quote one path segment, so an id can never add a path of its own. */
    private static String segment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> object(Object value) {
        return (Map<String, Object>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> array(Object value) {
        return (List<Map<String, Object>>) value;
    }

    @SuppressWarnings("unchecked")
    private static List<Object> items(Object value) {
        return (List<Object>) value;
    }

    /** A JSON reader and writer: enough for this contract, and the reason this file has no imports. */
    static final class Json {
        private final String text;
        private int at;

        private Json(String text) {
            this.text = text;
        }

        /**
         * Read one JSON document.
         *
         * @param text the document
         * @return maps, lists, strings, doubles, booleans — or {@code null}
         */
        static Object parse(String text) {
            Json reader = new Json(text);
            Object value = reader.value();
            reader.space();
            if (reader.at != text.length()) {
                throw new IllegalArgumentException("trailing content at " + reader.at);
            }
            return value;
        }

        /**
         * Write a JSON document.
         *
         * @param value maps, lists, strings, numbers, booleans — or {@code null}
         * @return the document
         */
        static String write(Object value) {
            StringBuilder out = new StringBuilder();
            write(value, out);
            return out.toString();
        }

        private static void write(Object value, StringBuilder out) {
            if (value == null) {
                out.append("null");
            } else if (value instanceof String text) {
                out.append('"').append(escape(text)).append('"');
            } else if (value instanceof Boolean || value instanceof Integer || value instanceof Long) {
                out.append(value);
            } else if (value instanceof Number number) {
                double found = number.doubleValue();
                out.append(found == Math.rint(found) ? Long.toString((long) found) : found);
            } else if (value instanceof Map<?, ?> map) {
                out.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    out.append('"').append(escape(String.valueOf(entry.getKey()))).append("\":");
                    write(entry.getValue(), out);
                }
                out.append('}');
            } else if (value instanceof Iterable<?> items) {
                out.append('[');
                boolean first = true;
                for (Object item : items) {
                    if (!first) {
                        out.append(',');
                    }
                    first = false;
                    write(item, out);
                }
                out.append(']');
            } else {
                out.append('"').append(escape(String.valueOf(value))).append('"');
            }
        }

        private static String escape(String text) {
            StringBuilder out = new StringBuilder(text.length());
            for (int i = 0; i < text.length(); i++) {
                char character = text.charAt(i);
                switch (character) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (character < 0x20) {
                            out.append(String.format(Locale.ROOT, "\\u%04x", (int) character));
                        } else {
                            out.append(character);
                        }
                    }
                }
            }
            return out.toString();
        }

        private Object value() {
            space();
            if (at >= text.length()) {
                throw new IllegalArgumentException("unexpected end of document");
            }
            char character = text.charAt(at);
            return switch (character) {
                case '{' -> object();
                case '[' -> list();
                case '"' -> string();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                case 'n' -> literal("null", null);
                default -> number();
            };
        }

        private Map<String, Object> object() {
            Map<String, Object> found = new LinkedHashMap<>();
            at++;                                  // {
            space();
            if (at < text.length() && text.charAt(at) == '}') {
                at++;
                return found;
            }
            for (;;) {
                space();
                String name = string();
                space();
                expect(':');
                found.put(name, value());
                space();
                char separator = text.charAt(at++);
                if (separator == '}') {
                    return found;
                }
                if (separator != ',') {
                    throw new IllegalArgumentException("expected , or } at " + (at - 1));
                }
            }
        }

        private List<Object> list() {
            List<Object> found = new ArrayList<>();
            at++;                                  // [
            space();
            if (at < text.length() && text.charAt(at) == ']') {
                at++;
                return found;
            }
            for (;;) {
                found.add(value());
                space();
                char separator = text.charAt(at++);
                if (separator == ']') {
                    return found;
                }
                if (separator != ',') {
                    throw new IllegalArgumentException("expected , or ] at " + (at - 1));
                }
            }
        }

        private String string() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (at < text.length()) {
                char character = text.charAt(at++);
                if (character == '"') {
                    return out.toString();
                }
                if (character != '\\') {
                    out.append(character);
                    continue;
                }
                char escape = text.charAt(at++);
                switch (escape) {
                    case '"', '\\', '/' -> out.append(escape);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape at " + (at - 1));
                }
            }
            throw new IllegalArgumentException("unterminated string");
        }

        private Object number() {
            int start = at;
            boolean fractional = false;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                char character = text.charAt(at);
                fractional |= character == '.' || character == 'e' || character == 'E';
                at++;
            }
            String digits = text.substring(start, at);
            // A whole number stays whole: `rows` reading back as 10 rather than 10.0 is the difference
            // between an id and a float, and this contract's integers are mostly ids.
            return fractional ? Double.valueOf(digits) : Long.valueOf(digits);
        }

        private Object literal(String expected, Object value) {
            if (!text.startsWith(expected, at)) {
                throw new IllegalArgumentException("unexpected token at " + at);
            }
            at += expected.length();
            return value;
        }

        private void expect(char character) {
            if (at >= text.length() || text.charAt(at) != character) {
                throw new IllegalArgumentException("expected " + character + " at " + at);
            }
            at++;
        }

        private void space() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }
    }
}
