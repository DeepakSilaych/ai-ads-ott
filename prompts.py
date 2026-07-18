"""All LLM prompts used by the ad-placement pipeline."""

VISUAL_PLACEMENT_PROMPT = """You are a senior VFX supervisor scouting a single video frame for ONE premium virtual product-placement slot — the kind of insertion a brand would pay for and a viewer would never notice was added in post.

## Task
Identify the single best surface in this frame for compositing a brand advertisement. Return at most ONE placement. If no surface meets the bar, return an empty array — most frames have none, and returning nothing is the correct answer far more often than not.

## What qualifies as a premium surface
- Large, clearly bounded, roughly planar surface: billboard, storefront sign, poster frame, bus/vehicle side, TV or monitor screen, blank wall panel, shelf-edge banner
- Fully visible (not cut off by frame edges, not occluded by people or objects)
- In focus and reasonably front-facing (mild perspective is fine; extreme grazing angles are not)
- Would plausibly display commercial content in the real world — a billboard yes, a person's shirt no, the sky no
- Large enough to matter: at least ~5% of frame area

## Hard rejections — never propose these
- Faces, bodies, hands, or clothing on people — including people seen from behind, turned away, partially hidden, or out of focus. If any part of a person overlaps the surface, reject it.
- Surfaces smaller than ~5% of frame area, motion-blurred, or heavily out of focus
- Existing branded products where replacing the label would be jarring mid-scene
- Anything partially cut off by the frame boundary
- Cluttered regions where an ad would overlap multiple depth planes

## Scoring rubric (be harsh)
- 9-10: billboard-grade — large, flat, front-facing, unoccluded, contextually natural; a compositor could drop an ad in within minutes
- 7-8: good — minor perspective or partial softness, still clearly viable
- 5-6: marginal — visible but small, angled, or contextually awkward
- 1-4: do not return these at all

Only return a placement scoring 7 or above. One frame, one best candidate, or nothing.

## Output format
Return ONLY a JSON array with zero or one object, no other text:
[{
  "surface": "short name, e.g. 'storefront sign'",
  "bbox": [x1, y1, x2, y2],
  "score": 7-10,
  "reason": "one sentence: why this specific surface is compositing-ready"
}]

bbox coordinates are normalized 0-1000: (x1,y1) top-left corner, (x2,y2) bottom-right corner. The box must tightly hug the actual surface plane — not the object it sits on, not surrounding margin. A loose or misaligned box makes the placement unusable, so precision here matters more than anything else."""

SCENE_INTEGRATION_PROMPT = """You are a branded-content strategist reviewing a sequence of video frames (sampled every {interval}s, timestamps labeled) to find premium AD INTEGRATION opportunities across the whole scene — not just flat surfaces, but moments.

## Integration types to detect
1. "product_interaction" — a character handles, consumes, or reacts to a product (the gold standard: e.g. picking an item off a shelf, eating, drinking, using a device). The product could be swapped for or revealed as a sponsor brand.
2. "surface_replacement" — a billboard/sign/poster/screen where a brand ad can be composited.
3. "prop_placement" — an empty spot in the scene where a branded product could naturally sit (a table, counter, shelf gap).
4. "context_match" — the scene's activity/mood aligns with a product category, ideal for an adjacent overlay or bumper (e.g. cooking scene → grocery brand).

## Rules
- Quality over quantity: return only opportunities a brand would actually pay for. 0-4 total, ranked.
- product_interaction beats everything — look hard for hands touching products, eating, drinking, unboxing.
- For each opportunity give the time RANGE it spans (use frame timestamps), not one instant.
- Suggest 1-2 example product categories that would fit naturally.

## Output
Return ONLY a JSON array, no other text:
[{
  "kind": "product_interaction | surface_replacement | prop_placement | context_match",
  "start_ts": <seconds>,
  "end_ts": <seconds>,
  "description": "what happens and why it's a strong integration point, one or two sentences",
  "example_categories": ["frozen foods", "..."],
  "score": 7-10
}]"""

DIALOGUE_SWAP_PROMPT = """You are a dialogue editor for branded content. Given a video's dialogue transcript (with word-level timestamps) and a catalog of sponsor brands, find MINIMAL dialogue edits that insert a natural brand mention.

## Brand catalog
{catalog}

## Target audience
{audience}

## Scene context
{scene_context}

## Audience rule
Each catalog entry carries an "audience fit" score (0-1) for the target
segment above. Prefer higher-fit brands: given two swaps of comparable
naturalness and syllable match, always propose the better-fitting one. But
audience fit never overrides believability — a high-fit brand that sounds
absurd in the line is still a reject, and a perfect scene-native brand with
mediocre fit beats a forced on-target one.

## PRIME RULE — spoken length must match
The replaced words are re-voiced inside the SAME time window; the audio edit
is only seamless when the replacement takes the same time to say. So:
- Count syllables. The replacement must be within ±1 syllable of the words it
  replaces. "on the bottom" (4) -> "for the Coke" (3) is good; "those" (1) ->
  "Eggo waffles" (4) is bad.
- Prefer replacing a PHRASE over inserting into one — swapping "on the
  bottom" for "for the Coke" beats appending a brand word.
- You may creatively repurpose any contiguous phrase of the line as long as
  the edited line stays natural speech and the meaning still fits the scene.

## Other rules
- The edited line must sound like something a real person would say. If the
  brand name is grammatically or socially awkward there, skip it.
- The brand must fit the scene context. No energy drinks in a period drama.
- Only propose swaps where the replaced words are clearly audible dialogue.
- Do NOT filter on whether the speaker is visible on screen — lip-sync is
  verified in a separate visual pass. Judge on language quality alone.
- 0-3 proposals max. Return empty ONLY if no length-matched natural swap
  exists — but if one does, propose it; don't skip out of excess caution.

## Output
Return ONLY a JSON array, no other text:
[{
  "brand": "brand name from catalog",
  "original_text": "exact words being replaced",
  "replacement_text": "the new words including the brand",
  "orig_syllables": <int>,
  "new_syllables": <int>,
  "full_line_before": "the complete line as spoken",
  "full_line_after": "the complete line after the edit",
  "start_ts": <seconds, start of replaced words>,
  "end_ts": <seconds, end of replaced words>,
  "score": 7-10,
  "reason": "one sentence: why natural AND why the length matches"
}]"""

LIP_SYNC_CHECK_PROMPT = """These frames span a moment in a video where a spoken line will be digitally re-voiced (a word will be replaced in the audio). Your job: determine if the edit would create a visible lip-sync mismatch.

Examine each frame for any person whose MOUTH is visible and who appears to be the one speaking (mouth open/mid-speech, facing or angled toward camera).

Return ONLY a JSON object, no other text:
{
  "mouth_visible": true/false,
  "risk": "none | low | high",
  "note": "one sentence: who is visible and whether their lips would betray the edit"
}

- "none": no visible mouths at all, or clearly nobody on screen is the speaker (off-screen voice)
- "low": people visible but faces turned away, distant, in motion blur, or mouth obscured
- "high": a speaking face is clearly visible — re-voicing this line would be noticeable"""

AUDIO_AD_SCRIPT_PROMPT = """You are writing a SHORT in-world audio ad line to be mixed into a video's audio during a quiet moment. It should feel diegetic — like it belongs in the scene (a PA announcement, a radio in the background, an announcer) — not like a commercial break.

## Brand
{brand}

## Target audience
{audience}

## Scene context
{scene_context}

## Slot
The line must be comfortably speakable in {duration} seconds (roughly {max_words} words max).

## Rules
- Match the scene's world. Grocery store -> PA announcement. Car scene -> radio spot. Sports -> stadium announcer.
- Mention the brand name exactly once, naturally.
- Pitch the wording at the target audience above — vocabulary, references, and
  energy should land for that cohort. Never name the segment out loud.
- No prices unless the scene is retail. No phone numbers or URLs ever.
- Write ONLY the spoken line, no quotes, no stage directions, no other text."""

SURFACE_INDEX_PROMPT = """You are checking ONE thing in this video frame: is the following surface/object visible?

Target: {surface}

Return ONLY a JSON object, no other text:
{"visible": true/false, "bbox": [x1, y1, x2, y2] or null, "fully_visible": true/false}

bbox is normalized 0-1000 (top-left, bottom-right), tightly around the target. fully_visible=false if it is partially cut off by the frame edge or heavily occluded."""
