# Splitting the backend page into a how-to, a pool rule, and the records

Date: 2026-10-17. Topic: `deploy/gpu-backends.md` had grown three kinds of content — operating
steps, an inventory of our own machines, and design arguments with measurements — and the inventory
had to be maintained by hand in a document nobody re-read. What moved where, and why.

## The judgement

The page is read by someone about to touch a machine, who wants a sequence, not an argument. An
inventory is read by an agent or an operator asking *what exists now*, and a measurement is read by
someone asking *how was that established*. Three different questions, three different lifetimes: the
sequence changes rarely, the inventory changes whenever a machine joins, and a measurement never
changes at all. So:

- **`deploy/gpu-backends.md`** is the sequence. §1 path, §2 Docker per OS, §3 the image, §4 the
  gateway's list, §5 links. It states a convention in one line and links the reasoning.
- **`.agents/rules/backend-pool.md`** is the pool: which machines, which addresses, who may reach
  them, one router per fault domain, the card-minus-1-GB rule, and the token-not-address decision.
  It is the one rules file that is not a package's, which required a sentence in `AGENTS.md` saying
  so — otherwise it is a sixth file nothing points to.
- **References** keep the evidence: the routers in `2026101610`, the 5090's bring-up in
  `2026101621`, the 3090 in `2026101709`, the port move in `2026101622`, the VRAM figures in
  `2026101612`.

## What moved rather than disappeared

- The inventory table, the 5090's resident correction, and the fault-domain paragraph are now the
  rules file. Its table has no "last measured" narrative column: a status cell and a link, because a
  row that carries a paragraph stops being scanned.
- **The allowlist argument had no home at all** — it existed only on that page. The measurement
  behind it (one machine's `sshd` logging the caller's tailnet address, another logging
  `192.168.253.1`, and the container behind Docker Desktop seeing a third) is now a section of
  `2026101621`, and the conclusion is one bullet in `backend-pool.md`. This is the failure mode of
  moving content: a decision whose reasoning lives only where the decision is used has to be checked
  for before the page is cut, not after.
- The routing/timing narrative (61.2 s, the 3 s connect bound, the loading retry) left the page
  entirely: `gateway.md` carries the invariant and `2026101621` the measurements. The page keeps one
  operational consequence — a stopped backend still listed costs the pool one connect per window —
  because that is the sentence that changes what a person does.

## What the page still says that a rule cannot

The three facts to prove in order (Docker runs, the GPU is inside a container, the port answers from
the *gateway host*), the full `docker run` line with what each flag becomes in `docker inspect`, and
the probe that sends an invalid body so a token check can be tested without running an inference.
Each is a step whose value is that it is done in that order.

## Next time

Ask of each paragraph: is this a step, a fact that must stay current, or a measurement? A page that
mixes them will grow back, because every new finding has a natural place in the page that discusses
its subject.
