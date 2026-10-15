// Decode a COCO run-length encoded mask, with no dependency.
//
// GWHS stores every object as the compressed counts string COCO defines, and hosted SAM
// endpoints answer masks in the same format. The runs are read over the mask flattened
// column-major (COCO's Fortran order), so the flat result indexes as row + column * height.
fun rleDecode(counts: String, height: Int, width: Int): BooleanArray {
    val runs = ArrayList<Int>()
    var i = 0
    while (i < counts.length) {
        var value = 0
        var shift = 0
        var chunk: Int
        do {
            chunk = counts[i].code - 48
            value = value or ((chunk and 0x1f) shl shift)
            shift += 5
            i++
        } while (chunk and 0x20 != 0)
        if (chunk and 0x10 != 0) value = value or (-1 shl shift)   // sign-extend
        runs.add(value + if (runs.size > 2) runs[runs.size - 2] else 0)
    }
    val mask = BooleanArray(height * width)
    var position = 0
    for (run in runs.indices) {
        if (run % 2 == 1) {
            mask.fill(true, position, position + runs[run])   // an array fill, not a per-pixel loop
        }
        position += runs[run]
    }
    return mask
}
