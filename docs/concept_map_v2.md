# Concept Map V2

Source-locked concept generation replacing the multi-pass staged pipeline.

## Module

`backend/app/services/concept_map_v2.py`

## Flow

1. Parse MMD → lock `expected_topics` from section headings (`_topic_headings`)
2. Extract Question / Task Inventory (existing pass, unchanged)
3. Build `RunConfig` (board, class, subject, chapter, publication, duration, topics)
4. **One master LLM call** — `build_concept_map_prompt()`
5. **Validate** — `validate_concept_map()` (deterministic)
6. **Optional repair** — `build_repair_prompt()` if validation fails
7. **Render** — `render_concept_description()` (Description + Achieving Mastery + Misconception + Types once)
8. **Tags in code** — chapter tag, topic tag, concept tag (never from the model)

## Output shape

```
Description: <body>
Achieving Mastery: <mastery> // Misconception: <one specific error> // Types: Type 01: ... Case 01: ...
```

## Config requirements

| Field | Source |
|---|---|
| `expected_topics` | MMD section headings (deterministic) |
| `chapter_duration_minutes` | Chapter DB field, or `max(180, topics×60)` fallback |
| `publication` | Upload `source_book` (defaults to NCERT) |
| `chapter_tag` | Code: `09_Mathematics_CBSE_NCERT` |

## Entry point

`generation.concepts_from_mmd()` live path → `concept_map_v2.generate_concept_map_v2()`
