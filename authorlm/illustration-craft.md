# Illustration description craft — all manuscripts

Guidelines for WRITING illustration descriptions (the spot-finder and
the chat critique both read this file; edit freely — general rules
first, model-specific carveouts in `## Model:` sections, of which only
the active image model's section is used).

- Concrete nouns with bound attributes — "a gray hooded sweatshirt and
  running shoes", never abstractions like "modern clothes"
  (abstractions get resolved in the style's era, not the text's).
- Positive phrasing only: say what IS in the image; never "no X" or
  "without X" (negations plant the very thing they forbid).
- One idea per clause; short declarative clauses.
- Camera and composition language is welcome: angle, distance, light
  source, where the negative space sits.
- Impossible geometry (Penrose stairs, Escher constructions) cannot be
  described into existence: models repair the paradox into ordinary
  geometry. Condition on a reference figure (public-domain diagram as
  the source image) and frame the prompt as an edit that preserves the
  line geometry exactly (lesson of 2026-08-10, the Ladder of Sermon 7).

## Model: gpt-image-2

- Default model (config `image_model`). Renders lettering reliably:
  short inscriptions and labels may be specified when the metaphor
  calls for them.

## Model: gemini/gemini-3-pro-image-preview

- Strong period pull from style vocabulary ("engraving", "woodcut"):
  when the scene is contemporary, name the era and specific garments
  explicitly in the description.
- Repairs impossible geometry even when seeded with the correct figure
  (2026-08-10: Penrose staircase normalized to up-and-down flights).
  For paradox images, generate externally (OpenAI's image model held
  the closed circuit) and import the file as a candidate.
