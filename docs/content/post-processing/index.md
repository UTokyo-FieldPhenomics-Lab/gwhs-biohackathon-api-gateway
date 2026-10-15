# Post-processing

What the API returns is data, not a picture. A semantic dataset hands you a mask image whose colours
are class names; an instance dataset hands you an object's geometry as a compressed run-length
string. These are the two conversions most projects start with, in Python, JavaScript, and Kotlin —
and each snippet is a file you can download.

Both start from pixels you have already decoded — Pillow, a browser canvas, `Bitmap.getPixels` — and
the class mapping always comes from the dataset's own description, `GET /datasets/<id>`, never from a
figure or a blog post.

## A palette mask, one boolean array per class

`gwfss`'s annotation is an image: three channels, every pixel one of the four colours the description
publishes. Splitting it gives one boolean array per class, and the function refuses a colour the
dataset never declared rather than labelling that pixel silently.

=== "Python"

    ```python
    import requests
    from io import BytesIO

    import numpy as np
    from PIL import Image
    from label_masks import label_masks

    headers = {"X-API-Key": API_KEY}
    description = requests.get(f"{API}/datasets/gwfss", headers=headers).json()    # the palette
    row = requests.get(f"{API}/datasets/gwfss/images/0", headers=headers).json()   # one row's URLs

    mask = np.asarray(Image.open(BytesIO(requests.get(row["mask_url"]).content)))
    for name, pixels in label_masks(mask, description["classes"]).items():
        print(name, pixels.sum())
    # background 146817 · head 0 · stem 1920 · leaf 113407
    ```

    [Download label_masks.py](label_masks.py){: download="label_masks.py" }

=== "JavaScript"

    ```js
    // get(path) is your own helper: fetch(API + path, { headers }).then((r) => r.json())
    const headers = { "X-API-Key": API_KEY };
    const description = await get("/datasets/gwfss");     // the palette
    const row = await get("/datasets/gwfss/images/0");    // one row's URLs

    const image = await createImageBitmap(await (await fetch(row.mask_url)).blob());
    const canvas = new OffscreenCanvas(image.width, image.height);
    const context = canvas.getContext("2d");
    context.drawImage(image, 0, 0);
    const { data, width, height } = context.getImageData(0, 0, image.width, image.height);

    for (const [name, pixels] of Object.entries(labelMasks(data, width, height, description.classes))) {
      console.log(name, pixels.reduce((sum, value) => sum + value, 0));
    }
    ```

    [Download label_masks.js](label_masks.js){: download="label_masks.js" }

=== "Kotlin"

    ```kotlin
    // getJson(path) is your own helper: HttpURLConnection with the headers, then JSONObject(text)
    val headers = mapOf("X-API-Key" to API_KEY)
    val description = getJson("/datasets/gwfss")      // the palette
    val row = getJson("/datasets/gwfss/images/0")     // one row's URLs

    val mask = BitmapFactory.decodeStream(URL(row.getString("mask_url")).openStream())
    val pixels = IntArray(mask.width * mask.height)
    mask.getPixels(pixels, 0, mask.width, 0, 0, mask.width, mask.height)

    val classes = description.getJSONArray("classes").let { array ->
        (0 until array.length()).map { index ->
            val entry = array.getJSONObject(index)
            mapOf("name" to entry.getString("name"), "color" to entry.getString("color"))
        }
    }
    for ((name, flagged) in labelMasks(pixels, classes)) {
        println("$name ${flagged.count { it }}")
    }
    ```

    [Download label_masks.kt](label_masks.kt){: download="label_masks.kt" }

## An object's RLE geometry, as a mask

An instance dataset carries geometry, not a mask image, so a detail response holds one entry per
object:

```json
{ "idx": 0, "category": "spikelet_-_side1", "bbox": [231.0, 923.0, 126.0, 162.0],
  "area": 12074.0, "attributes": ["Green-Spikelet"],
  "rle": { "size": [2509, 592], "counts": "Zofa05S…" } }
```

`rle.size` is `[height, width]`, and `rle.counts` is the annotation's compressed string, verbatim —
COCO's format, which is also what hosted SAM endpoints answer. Decoding it gives the object's own
mask, and its pixel count equals the `area` beside it: the cheapest check that a decoder is right,
and 12,074 for the object above.

=== "Python"

    ```python
    from rle_decode import rle_decode

    annotation = requests.get(f"{API}/datasets/{DATASET}/images/{ROW}", headers=headers).json()
    object_ = annotation["objects"][0]
    height, width = object_["rle"]["size"]

    flat = rle_decode(object_["rle"]["counts"], (height, width))   # a flat boolean array
    mask = flat.reshape(height, width, order="F")                  # COCO is column-major
    assert mask.sum() == object_["area"]
    ```

    [Download rle_decode.py](rle_decode.py){: download="rle_decode.py" }

=== "JavaScript"

    ```js
    const annotation = await get(`/datasets/${DATASET}/images/${ROW}`);
    const object = annotation.objects[0];
    const [height, width] = object.rle.size;

    const mask = rleDecode(object.rle.counts, height, width);   // Uint8Array, column-major
    const area = mask.reduce((sum, value) => sum + value, 0);
    console.log(area === object.area);
    ```

    [Download rle_decode.js](rle_decode.js){: download="rle_decode.js" }

=== "Kotlin"

    ```kotlin
    val annotation = getJson("/datasets/$DATASET/images/$ROW")
    val obj = annotation.getJSONArray("objects").getJSONObject(0)
    val rle = obj.getJSONObject("rle")
    val size = rle.getJSONArray("size")
    val height = size.getInt(0)
    val width = size.getInt(1)

    val mask = rleDecode(rle.getString("counts"), height, width)   // BooleanArray, column-major
    println(mask.count { it } == obj.getDouble("area").toInt())
    ```

    [Download rle_decode.kt](rle_decode.kt){: download="rle_decode.kt" }

## What is worth optimizing

Both jobs are one pass over the data if you write them that way, and the honest answer differs by
language. Measured on one 512×512 mask and one 2509×592 object, in NumPy and in Node:

| job | approach | Python | JavaScript |
|---|---|---|---|
| palette mask | one pass per class | 17.3 ms | **1.7 ms** |
| palette mask | one pass over packed colours | **2.8 ms** | 2.8 ms |
| RLE | one assignment per run | 23.1 ms | **0.15 ms** |
| RLE | one cumulative sum over run boundaries | **4.5 ms** | — |

Python pays for what the per-class version allocates: a three-channel temporary per class, and a list
of booleans per object. NumPy removes both, which is why the Python files here are the vectorized
ones. JavaScript's typed arrays already do that work, so there the packed-colour lookup is *slower*
than four integer comparisons — a table that beats them would need 2²⁴ entries.

Neither number is what your program will spend its time on: fetching and decoding an image costs tens
of milliseconds, whatever you do afterwards. Profile before optimizing — but in Python, avoid the
version that loops over pixels, which is what these files did before they were measured.

## The files

| file | language | what it does |
|---|---|---|
| `label_masks.py`, `label_masks.js`, `label_masks.kt` | Python, JavaScript, Kotlin | split a palette mask into one boolean array per class |
| `rle_decode.py`, `rle_decode.js`, `rle_decode.kt` | Python, JavaScript, Kotlin | decode a COCO counts string into a flat mask, column-major |
