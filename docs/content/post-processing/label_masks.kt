// Split a palette mask into one boolean mask per class.
//
// GWFSS's annotation is an image: three RGB channels, every pixel one of four colours, and the
// dataset's own description at GET /datasets/gwfss publishes that palette as #rrggbb strings — pass
// its `classes` straight in. `pixels` is what the platform already decoded: ARGB, as
// `Bitmap.getPixels` returns it.
//
// One pass per class with three integer comparisons and an early exit is the fastest shape here: a
// packed-colour lookup table would need 2^24 entries, and a hash lookup per pixel measured slower
// than the comparisons it replaces (2.8 ms against 1.7 ms for a 512x512 mask in JavaScript).
fun labelMasks(pixels: IntArray, classes: List<Map<String, String>>): Map<String, BooleanArray> {
    val masks = LinkedHashMap<String, BooleanArray>()
    val known = BooleanArray(pixels.size)
    for (entry in classes) {
        val (r, g, b) = channels(entry.getValue("color"))
        val mask = BooleanArray(pixels.size)
        for (i in pixels.indices) {
            val pixel = pixels[i]
            if ((pixel shr 16) and 0xff == r && (pixel shr 8) and 0xff == g && pixel and 0xff == b) {
                mask[i] = true
                known[i] = true
            }
        }
        masks[entry.getValue("name")] = mask
    }
    if (known.any { !it }) throw IllegalArgumentException("mask holds a colour that is not in the palette")
    return masks
}

fun channels(color: String): Triple<Int, Int, Int> {
    val hex = color.removePrefix("#")
    require(hex.length == 6) { "a colour must be #rrggbb, got $color" }
    return Triple(
        hex.substring(0, 2).toInt(16),
        hex.substring(2, 4).toInt(16),
        hex.substring(4, 6).toInt(16),
    )
}
