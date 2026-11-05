# WeVisOne-VL: masks work, after one fp16 fix

Whether Tencent's `WeVisOne-VL-4B` can turn a text prompt into instance masks on wheat, measured on
the DGX's V100. The machine itself is [`2026110418_dgx-station-v100-reachable.md`](2026110418_dgx-station-v100-reachable.md),
which recorded the earlier finding that it "detects and points" but had not produced a mask.

## What ran

One card, `--gpus device=1`, on `gwhd` row 4 — a 1024×1024 close-up with about 40 wheat heads,
overlapping and awned, the hard case for a boundary.

- `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime`, **torch kept at 2.6.0** — this is the build whose
  `get_arch_list()` still carries `sm_70`. The repository pins `torch==2.11.0` and `scipy==1.18.1`;
  `scipy` 1.18 requires Python 3.12 and the image has 3.11, which failed the pinned set outright, so
  torch 2.11 was never installed — and it would have to be checked for Volta before it is trusted.
  `transformers==5.12.1` and the other pins did install, so the API the decoder hooks into is the one
  the release was written against.
- The package from <https://github.com/Tencent/WeVisOne-VL> (`pip install --no-deps -e .`), plus
  `mask_decoder.pt` from the model repository — 38.5 MB, 9.6M parameters, fetched on first load.

## The mask came back all NaN, and the cause is the dtype of one buffer

`dtype="float16"` on the V100 produced 43 correct boxes and 43 NaN masks. Tracing each decoder stage
(`visual_encoder` → `query_builder` → `transformer` → `pixel_fusion` → `mask_predictor` → `highres_refine`)
put the first NaN in `query_builder`'s **position** output, with `content` clean and every input clean:
`t_seg` finite, `boxes01` finite, visual features finite.

`FourierPE1D` registers `2.0 ** arange(32)` as a **non-persistent** buffer. `WeVisOneVL` then calls
`.to(device, dtype=float16)` on the decoder, which casts that buffer too:

```
torch.float16   2**15=32768.0   2**16=inf        2**31=inf        inf=16
torch.bfloat16  2**15=32768.0   2**16=65536.0    2**31=2^31       inf=0
```

Sixteen of the thirty-two frequencies become `inf`, `2π·x·inf` is `inf`, `sin(inf)` is NaN, and the
NaN rides the position embedding into every query and every pixel. **It is not a precision problem at
all** — the decoder already runs its whole forward pass in fp32 (`self.float()` plus
`torch.autocast(enabled=False)`), so only the cast of that buffer decides it. bf16 has fp32's exponent
range, which is why the release's default `dtype="bfloat16"` never shows this: **the bug is reachable
only on a card that has to run fp16**, which in this pool is the V100 alone.

The fix is to rebuild the frequencies in float32 where they are used, instead of trusting the buffer
after the module was cast:

```python
def fixed_forward(self, values):
    freqs = 2.0 ** torch.arange(self.frequencies.numel(),
                                device=values.device, dtype=torch.float32)
    phase = 2.0 * math.pi * values[..., None].float() * freqs
    return torch.cat([phase.sin(), phase.cos()], dim=-1).to(values.dtype)
```

With that in place: 43 masks, zero NaN, and the masks are good.

## What it produces, and what it costs

| | `WeVisOne-VL-4B` | `WeVisOne-VL-2B` |
|---|---|---|
| weights in VRAM | 9.07 GB | 4.57 GB |
| generate the 43 boxes | 22.7 s | 15.6 s |
| decode the masks | 3.5 s | 3.0 s |
| **one image** | **26.2 s** | **18.6 s** |
| peak VRAM (`decoder_batch_size=4`) | 15.0 GB | 10.5 GB |
| instances found | 43 | 38 |
| median mask area | 0.23 % of the frame | 0.19 % |

- **43 heads in 22.7 s is the token budget, not the pixels.** Each box costs four coordinate tokens
  plus `<OBJ>` — about 270 tokens at the ~12 tok/s the card sustains. Dense scenes scale with the
  object count, not with the image size, so a field view with 60 heads is ~35 s and a sparse one is
  seconds.
- **`decoder_batch_size` is a real memory knob.** The default 16 needs 29.4 GB for 43 instances and
  OOMs outright once its inputs are float32; 4 needs 15.0 GB. A 24 GB card must declare 4.
- **4B is the model to use.** 2B found 38 instances and labelled leaves as heads — several boxes sit
  on awns and blades rather than on grain. The speed it buys is 30 %, not worth the errors.

The answer format is the one the earlier note recorded, with the mask decoded from the same tokens:
`<ref>wheat head</ref><box><145><790><216><836><OBJ>,…</box>`, 1149 characters for 43 instances,
coordinates as 0–1000 integers, `parse_seg_boxes` returning them normalized to `[0, 1]`. Masks come
back at the image's own resolution as float probability maps — no upsampling needed, and no SAM.

Visually the masks follow each head's outline and separate overlapping heads; `segmentation` and
`matting` returned the same 43 instances here, which is expected on an opaque subject. Both overlays
and the cut-out contact sheets are in `/ssdraid0/bench/wv/` on the DGX.

## What shipping it would take

It is a capability nothing else in the pool has: `gwfss-segformer-b4` is semantic (three classes, no
instances), `sam3.1-meta` needs points or a box as the prompt, and this is **text in, instances out**.
The gap to a `wevisone-vl` model id is a container, not research:

1. Vendor the package into the image and apply the Fourier fix as a patch, not a runtime monkeypatch.
2. Speak the backend protocol, decode each probability map to COCO RLE once, return one object per
   instance with `bbox`, `rle` and the query as `category`.
3. Declare `response_timeout_seconds: 60` — 26 s measured, and the gateway's default 10 s would cut
   a real image off. Declare `decoder_batch_size=4` and about 16 GB resident, so it does not share a
   24 GB card.
4. bf16 on Ampere and above needs no patch; fp16 on the V100 needs it. The dtype goes in
   `computed_by.dtype` like every other container.
5. The weights are Apache-2.0 and the model page must carry that notice, per
   [`models.md`](../rules/models.md).

Left on the machine: `wevisone-probe:20261105` (6.53 GB, the base image with the pins installed),
the container `wevisone-test` (stopped, still carrying `/work` and `/data` mounts), the clone and the
five scripts in `/ssdraid0/bench/wevisone/`, and the overlays in `/ssdraid0/bench/wv/`.
