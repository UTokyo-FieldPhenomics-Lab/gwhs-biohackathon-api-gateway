# Java client

One file, no dependency and no build file: `GlobalWheat.java` compiles with the JDK you already
have, and the same source runs on Android. It is the same API the other two clients speak, with masks
as a row-major `boolean[]` and backbone grids as `float[][][]`.

## Load it

```bash
curl -O https://docs.global-wheat.com/clients/GlobalWheat.java
javac GlobalWheat.java
```

Drop the file into your source tree and call it. On Android, declare the `INTERNET` permission and
call the client off the main thread, which is where the network belongs anyway.

## A runnable example

With `GLOBAL_WHEAT_API_KEY` set, compile this beside the client and run it:

```java
import java.util.List;
import java.util.Map;

public class Main {
    public static void main(String[] args) {
        GlobalWheat.Client client = new GlobalWheat.Client(); // key and host from the environment

        Map<String, Object> row = client.image("gwhd", 0);
        List<?> objects = (List<?>) row.get("objects");
        System.out.println(row.get("width") + " x " + row.get("height")
                + " — " + objects.size() + " objects");

        Map<String, Object> answer =
                client.predict("sam3.1-meta", "gwhs-temp", 0, Map.of("prompt", "wheat head"));
        System.out.println(answer.get("model") + " at " + answer.get("resolution_used") + " px");
        for (Object found : (List<?>) answer.get("objects")) {
            Map<?, ?> object = (Map<?, ?>) found;
            System.out.println(object.get("idx") + " " + object.get("score") + " " + object.get("area"));
        }
    }
}
```

```bash
javac GlobalWheat.java Main.java
java Main
```

## Calls and parameters

The full method list is on the [overview](index.md#methods); these are the calls and options you
will use most:

| call | what you pass | notes |
|---|---|---|
| `new Client()` / `new Client(key)` | a token, or the environment | construction fails without a key |
| `image(dataset, row)` / `image(dataset, row, false)` | an address and a decode switch | `false` leaves `rle` as the wire wrote it |
| `images(dataset, limit, cursor, filters)` | `null` for no cursor or filters | the page reports `next_cursor` |
| `iter_images(dataset, limit, filters)` | the same filters | walks every page for you |
| `predict(model, dataset, row, params)` | a `Map<String, ?>` of the model's parameters | always decodes |
| `predict_file(model, file, params, false)` | a `File`, a parameter map and `resizeBeforeSubmit` | `false` sends the original bytes |
| `generate(model, params)` | `prompt`, and what the model declares | waits up to 180 seconds |
| `label_masks(pixels, width, height, dataset)` | an `int[]` of pixels and its sides | one `Mask` per class |

There is no `decode` switch on `predict`, `predict_file` or `generate`: the Java client always
decodes masks, counting grids and backbone grids, and leaves the raw fields beside them. A model's
parameters are a `Map` with the names `GET /models/<id>` publishes; an unknown name is a `400`, not a
silent default. A geometry parameter is one value: `Map.of("boxes", List.of(List.of(480, 290, 110,
360, 1)))`, never five repeated names.

## What is different here

- A mask is a `GlobalWheat.Mask` with `width`, `height`, a row-major `boolean[] data`, and
  `count()`; `count()` is the `area` beside it.
- A counting answer's `density.grid` is a `double[][]` of rows; a backbone's `features.grid` is a
  `GlobalWheat.Features` with a `float[][][] grid`.
- A generated picture is `image.picture`, a `byte[]` of the encoded file; `image.data` remains the
  base64 string. `GlobalWheat.decode_image(field)` decodes one field on its own.
- `GlobalWheat.ApiError` carries `status`, `code` and the API's own `message`; nothing is retried for
  you.

## Next

- [Overview](index.md) — the shared methods and calling conventions
- [Python](python.md) · [JavaScript](javascript.md) — the same calls in the other clients
- [Jupyter quickstart](../../examples/quickstart.md) — the same API, one step at a time
