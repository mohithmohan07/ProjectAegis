# API Key Sharing: 7 Users, Same Key

## Is it okay for 7 people to use the same API key?

**Yes, it can work**, with some caveats.

### What works
- **Cost**: All usage is billed to one account. Fine if you're on the same team.
- **Setup**: One key to manage. Store in Script Properties once.

### What to watch

| Concern | Impact | Mitigation |
|---------|--------|------------|
| **Rate limits** | OpenAI limits requests per minute per key. 7 users at once can hit limits. | Use smaller batches, add retries, or upgrade plan. |
| **Security** | Anyone with script access can see the key in Script Properties. | Use a service account or backend proxy if needed. |
| **Attribution** | Hard to see which user caused which cost. | Use separate keys per user if you need per-user billing. |

### Recommendation for 7 users

1. **Start with one shared key** – Usually fine for light/moderate use.
2. **Store in Script Properties** – Do not hardcode in code:
   ```
   File → Project properties → Script properties
   Add: OPENAI_API_KEY = sk-...
   ```
3. **Monitor usage** – Check [platform.openai.com/usage](https://platform.openai.com/usage) for spikes.
4. **If you hit rate limits** – Consider:
   - Per-user keys (each user adds their own in Script Properties)
   - A backend proxy (your server holds the key, Apps Script calls your API)

### Alternative: Per-user keys

If each user has their own OpenAI account:
- Add a setup flow: "Enter your API key" → save to **User Properties** (not Script Properties).
- `PropertiesService.getUserProperties().getProperty('OPENAI_API_KEY')`
- Each user's key stays private to their session.

---

**Note:** The current Assessment Tagging tool does **not** use GPT. It reads from chapter JSON files and builds Question Gist from `question_content`. If you add GPT later (e.g. for concept tagging), use the guidance above.
