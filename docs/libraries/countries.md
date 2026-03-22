# Countries

List all countries that have at least one approved library, with counts.

## `GET /api/v1/libraries/countries/`

Returns every country that contains at least one approved library, ordered by library count descending. Unlike the `/statistics/` endpoint (which caps at 10), this returns the full list.

**Authentication:** None required (public endpoint).

**Rate limiting:** Standard read rate limit applies.

**Caching:** Responses are cached for 1 hour (`Cache-Control: public, max-age=3600`).

### Response fields

| Field | Type | Description |
|-------|------|-------------|
| `items` | array | All countries with approved libraries |

Each **country** object contains:

| Field | Type | Description |
|-------|------|-------------|
| `country_code` | string | ISO 3166-1 alpha-2 code |
| `country_name` | string | Human-readable country name |
| `flag_emoji` | string | Unicode flag emoji |
| `count` | integer | Number of approved libraries |

### Example request

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/libraries/countries/
    ```

=== "Python"

    ```python
    import httpx

    response = httpx.get("https://bookcorners.org/api/v1/libraries/countries/")
    countries = response.json()["items"]
    for c in countries:
        print(f"{c['flag_emoji']} {c['country_name']}: {c['count']}")
    ```

### Example response

```json
{
  "items": [
    {
      "country_code": "DE",
      "country_name": "Germany",
      "flag_emoji": "\ud83c\udde9\ud83c\uddea",
      "count": 120
    },
    {
      "country_code": "FR",
      "country_name": "France",
      "flag_emoji": "\ud83c\uddeb\ud83c\uddf7",
      "count": 85
    },
    {
      "country_code": "IT",
      "country_name": "Italy",
      "flag_emoji": "\ud83c\uddee\ud83c\uddf9",
      "count": 42
    }
  ]
}
```
