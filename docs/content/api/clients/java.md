# Java client

One file, no dependency and no build file. Masks arrive as a row-major `boolean[]`; backbone grids as
a `float[][][]`. It targets the desktop JDK: Android has no `java.awt` or `javax.imageio`, so uploads
and picture decoding there need platform code.

## Setup

JDK 17 or newer.

```bash
curl -O https://docs.global-wheat.com/clients/GlobalWheat.java
export GLOBAL_WHEAT_API_KEY='YOUR_TEAM_KEY'
```

Drop the file into your source tree and compile it with the code that calls it. There is nothing to
add to a build file.

## Run an example

Save this as `Main.java` beside the client:

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

`gwhs-temp` is the development sample of `gwhs`, so this call works before the full annotation is
released. On your own photograph, use `predict_file("sam3.1-meta", new java.io.File("photo.jpg"),
Map.of("prompt", "wheat head"), true)`.

## Pass model parameters

Parameters are a `Map` with the names `GET /models/<id>` publishes. An unknown name is a `400`, not a
silent default.

```java
Map<String, Object> sam = client.predict("sam3.1-meta", "gwhs-temp", 0,
        Map.of("prompt", "wheat head", "confidence_threshold", 0.5));

Map<String, Object> counted = client.predict("tasselnetv4", "gwfss", 0,
        Map.of("boxes", List.of(List.of(312, 143, 44, 55, 1))));
```

A geometry parameter is one value — a `List` of entries — never repeated names. The [model
pages](../../models/index.md) say what each parameter does; [Model
parameters](../http/index.md#model-parameters) is the wire contract.

## Read decoded outputs

- A mask is a `GlobalWheat.Mask` with `width`, `height`, a row-major `boolean[] data` and `count()`;
  the raw `rle` stays beside it.
- A counting answer's `density.grid` is a `double[][]` of rows; a backbone's `features.grid` is a
  `GlobalWheat.Features` with a `float[][][] grid`.
- A picture is `image.picture`, a `byte[]`. On an edit that carries `frame`, it is already cropped to
  that box — the shape of the picture you sent — while `image.data` remains the model's full file.
- There is no `decode` switch: the Java client always decodes.

## When it goes wrong

A refusal raises `GlobalWheat.ApiError` with `status`, `code` and the API's own `message`. Nothing is
retried for you: `429` means wait, `503`/`504` are worth one more call, and `400` needs the request
fixed. [Refusals](../http/index.md#refusals) lists every code.

## Next

- [Overview](index.md) — the shared methods and calling conventions
- [Python](python.md) · [JavaScript](javascript.md) — the same calls in the other clients
- [Jupyter quickstart](../../examples/quickstart.md) — the same API, one step at a time
