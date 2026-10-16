# Part 3 questions for the organisers

The development ResPlan adapter currently generates node points because the
available records do not provide the competition's fixed-node input. Please
answer against the official schema, rule text, and checker where possible.

1. **Input and checker.** What is the official input schema for room polygons,
   fixed node coordinates, doors, open passages, walls, windows, excluded rooms,
   and units? Can you provide a complete sample input with its checker verdict
   and J components? Is the submitted JSON checked with the same geometry and
   numerical tolerances as the supplied Part 2 checker?
2. **Excluded-room cable transit.** May a cable pass through a bathroom or
   another excluded interior room when connecting assigned eligible rooms?
   If so, is that always permitted or only when no route through eligible rooms
   exists? The interim default is `forbid`; `avoid_if_possible` and `allow` are
   explicit comparison settings.
3. **Zone links.** Can two eligible rooms be linked into one hub territory
   through an excluded interior room? Do links need a labelled opening, or is
   geometric free-space adjacency sufficient?
4. **Open passages.** How are unlabelled open passages distinguished from a
   wall-only adjacency? How should the crossing line be encoded, especially
   when room polygons are separated by wall thickness or touch only at a
   corner? Does an open passage count as a zone link and as a crossing for
   route metadata?
5. **Coincident hub and node.** Are hub locations exactly on a fixed node
   allowed? If yes, are zero-length legs and repeated endpoint coordinates
   accepted in official output? The current default requires 0.002 m
   separation; setting it to zero restores comparison behavior.
6. **Runtime.** What is the per-plan wall-clock limit, and does it include
   input parsing, final checker validation, and output writing? Are Python
   startup and picture generation included? The current development acceptance
   budget is 60 seconds, with 10% reserved for final checking and output.
