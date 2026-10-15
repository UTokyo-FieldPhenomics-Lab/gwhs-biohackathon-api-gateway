# Reaching `api.*` from the UTokyo visitor network, when its firewall drops the public address

2026-10-15 23:15 JST. **A workaround for one network, not a practice.** The project's way to the API is
plain DNS to its public address, and that way works everywhere that has been measured except this
campus network. Nothing in the build, the deploy, or CI may depend on anything in this file, and none
of it belongs in `rules/`. Reach for it only when a live check has to be made from here and the page
comes up empty.

What it answers: [`2026101513_campus-path-blocks-the-eip.md`](2026101513_campus-path-blocks-the-eip.md)
measured that the name is right, the service answers from elsewhere, and this one path to the Elastic
IP is dropped. The address it falls back to is the tailnet peer inventoried in
[`2026101514_tailnet-topology-and-lab-reachability.md`](2026101514_tailnet-topology-and-lab-reachability.md).

## The symptom it is for

From here, `https://api.global-wheat.com/...` times out in a browser while the same name answers from
anywhere else, and `curl --resolve api.global-wheat.com:443:gwhs-host …` (the tailnet address)
succeeds immediately. A page that embeds the API therefore renders empty while the site around it is
fine — which reads like a broken frame and is not one.

## The recipe, in three parts

1. **Convince one browser instance, not the machine.** Since the name and the host are both healthy and
   only this path is dropped, bypass DNS for that one name:

   ```
   --host-resolver-rules="MAP api.global-wheat.com gwhs-host"
   ```

   Over the tailnet the host answers at once: a direct load of `/docs` renders and `/health` returns
   JSON. This is the whole fix on the API side; no host change was needed.

2. **Expect Chrome to refuse the frame next, and know why.** With the frame in play the request fails
   with `net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`: a public site is not allowed to load a frame
   from a *private* address, and `100.64.0.0/10` is private. That is an artifact of the fallback
   address — in production `api.*` is public, where the check does not apply — so the harness turns the
   check off **in the harness only**:

   ```
   --disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks,BlockInsecurePrivateNetworkRequests,PrivateNetworkAccessSendPreflights
   ```

   Feature names differ across Chrome versions; the error string is the reliable clue. A blank frame
   served from a private address is Chrome's judgement, not a defect in the page.

3. **Drive it with a browser that waits and can reach into the frame.** Dumping the DOM cannot see this
   page: `--virtual-time-budget` expires the *parent* while the cross-origin child is still loading, so
   the height message never arrives — and the same flags *do* show it for a localhost harness, which is
   exactly what makes it look like a page bug. Playwright waits in real time and reads the child frame:

   ```python
   # uv run --with playwright python <script>  — the system Chromium is enough, no browser download
   browser = p.chromium.launch(
       executable_path="/usr/bin/chromium",
       args=[
           "--host-resolver-rules=MAP api.global-wheat.com gwhs-host",
           "--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks",
       ],
   )
   page = browser.new_page(viewport={"width": 390, "height": 844})
   page.goto("https://docs.global-wheat.com/interactive/", wait_until="load")
   frame = page.frame_locator("#reference-frame iframe")  # cross-origin access is the point
   ```

   A participant token, when the check needs one, is read from the host over SSH and passed in an
   environment variable; it is never printed.

## What it can and cannot tell you

It is the only way from here to see what a participant sees — the frame's size, and what the commands
inside it answer — which is how the live acceptance check recorded in
[`2026101522_interactive-key-in-the-reference.md`](2026101522_interactive-key-in-the-reference.md) was
run.

It says nothing about the normal path. A pass here is not evidence that this network reaches the public
address, and the tailnet address is not something a participant can use; when a check has to reflect
what a participant experiences, run it from a network that reaches the public address.

## Do not

- Turn any of it into a rule, a deploy step, or a CI step. Every line of it exists because of one
  network's perimeter.
- Leave it behind: the flags belong to a throwaway profile and a throwaway process, and neither the
  machine's DNS nor anything serving has been touched.
