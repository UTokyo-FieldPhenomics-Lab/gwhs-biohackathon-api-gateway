// What resizing costs on the participant's machine, measured rather than assumed.
//
// Run it by hand, from this directory:
//
//   javac -d /tmp/bench ../GlobalWheat.java Bench.java && java -cp /tmp/bench Bench
//
// It generates its own fixtures — a 12 MP JPEG and a 12 MP PNG, never committed — warms the JVM
// with one preparation per image, and prints the median of five. The ceilings are deliberate
// overestimates: they catch a client that spends seconds resizing, not a twenty-percent
// regression. The numbers belong in the maintainers' notes, never in the participant-facing docs.

import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.Locale;
import javax.imageio.IIOImage;
import javax.imageio.ImageIO;
import javax.imageio.ImageWriteParam;
import javax.imageio.ImageWriter;
import javax.imageio.plugins.jpeg.JPEGImageWriteParam;
import javax.imageio.stream.MemoryCacheImageOutputStream;

public final class Bench {

    /** The side sam3.1-meta declares, which is the largest target any client resizes to. */
    private static final int SIDE = 1008;

    /** A 12 MP JPEG is decoded, turned, scaled and encoded inside this, after a warm-up. */
    private static final long JPEG_CEILING_MS = 800;

    /** A PNG has no decoder shortcut; it only has to stay in the same order of magnitude. */
    private static final long PNG_CEILING_MS = 1500;

    /** An image that already fits is read to its header and sent as it is. */
    private static final long PASSTHROUGH_CEILING_MS = 10;

    private static final int RUNS = 5;

    private Bench() {
        // A program, not a type: this class only has a main.
    }

    public static void main(String[] args) throws Exception {
        byte[] jpeg = photograph(4000, 3000, "jpeg");
        byte[] png = photograph(4000, 3000, "png");
        byte[] small = photograph(1008, 756, "jpeg");

        double jpegMs = median(jpeg, 4000, 3000);
        long jpegOut = outputSize;
        double pngMs = median(png, 4000, 3000);
        long pngOut = outputSize;
        double smallMs = median(small, 1008, 756);
        long smallOut = outputSize;

        System.out.printf(
                Locale.ROOT,
                "4000x3000 JPEG  %6.1f KB -> %6.1f KB %8.3f ms%n",
                jpeg.length / 1024.0,
                jpegOut / 1024.0,
                jpegMs);
        System.out.printf(
                Locale.ROOT,
                "4000x3000 PNG   %6.1f KB -> %6.1f KB %8.3f ms%n",
                png.length / 1024.0,
                pngOut / 1024.0,
                pngMs);
        System.out.printf(
                Locale.ROOT,
                "already 1008 px %6.1f KB -> %6.1f KB %8.3f ms%n",
                small.length / 1024.0,
                smallOut / 1024.0,
                smallMs);

        boolean passed = true;
        passed &= check("JPEG preparation", jpegMs, JPEG_CEILING_MS);
        passed &= check("PNG preparation", pngMs, PNG_CEILING_MS);
        passed &= check("pass-through", smallMs, PASSTHROUGH_CEILING_MS);
        if (!passed) {
            System.exit(1);
        }
    }

    /** The last output's size, so the median loop can report what it produced without a record. */
    private static long outputSize;

    /** Median of five preparations after one warm-up, and the last output's size. */
    private static double median(byte[] payload, int width, int height) {
        GlobalWheat.prepareUpload(payload, SIDE);   // warm-up: class loading, first decode
        double[] timings = new double[RUNS];
        byte[] prepared = payload;
        for (int run = 0; run < RUNS; run++) {
            long started = System.nanoTime();
            prepared = GlobalWheat.prepareUpload(payload, SIDE);
            timings[run] = (System.nanoTime() - started) / 1_000_000.0;
        }
        outputSize = prepared.length;
        Arrays.sort(timings);
        if (prepared == payload && (width > SIDE || height > SIDE)) {
            throw new IllegalStateException("a large image must not pass through unchanged");
        }
        return timings[RUNS / 2];
    }

    private static boolean check(String what, double measured, long ceiling) {
        boolean passed = measured <= ceiling;
        System.out.printf(
                Locale.ROOT,
                "%s %s: %.3f ms (ceiling %d ms)%n",
                passed ? "ok  " : "FAIL",
                what,
                measured,
                ceiling);
        return passed;
    }

    /** Build a large image with enough structure to decode like a photograph, as a file. */
    private static byte[] photograph(int width, int height, String format) throws IOException {
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < height; y++) {
            int green = y * 255 / height;
            for (int x = 0; x < width; x++) {
                image.setRGB(x, y, (x * 255 / width) << 16 | green << 8 | 96);
            }
        }
        ByteArrayOutputStream encoded = new ByteArrayOutputStream();
        if (!"jpeg".equals(format)) {
            ImageIO.write(image, format, encoded);
            return encoded.toByteArray();
        }
        ImageWriter writer = ImageIO.getImageWritersByFormatName("jpeg").next();
        try (MemoryCacheImageOutputStream stream = new MemoryCacheImageOutputStream(encoded)) {
            writer.setOutput(stream);
            JPEGImageWriteParam parameters = new JPEGImageWriteParam(Locale.ROOT);
            parameters.setCompressionMode(ImageWriteParam.MODE_EXPLICIT);
            parameters.setCompressionQuality(0.85f);
            writer.write(null, new IIOImage(image, null, null), parameters);
            stream.flush();
        } finally {
            writer.dispose();
        }
        return encoded.toByteArray();
    }
}
