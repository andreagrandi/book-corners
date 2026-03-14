# Statistics

Public, read-only endpoint that returns aggregate statistics about approved libraries on the platform.

## `GET /api/v1/statistics/`

Returns totals, a top-10 country ranking, and a cumulative growth time series.

**Authentication:** None required (public endpoint).

**Rate limiting:** Standard read rate limit applies.

### Response fields

| Field | Type | Description |
|-------|------|-------------|
| `total_approved` | integer | Total number of approved libraries |
| `total_with_image` | integer | Libraries with at least one photo (primary or community) |
| `top_countries` | array | Top 10 countries by library count |
| `cumulative_series` | array | Cumulative growth time series |
| `granularity` | string | `"daily"` or `"monthly"` depending on data span |

Each **country** object contains:

| Field | Type | Description |
|-------|------|-------------|
| `country_code` | string | ISO 3166-1 alpha-2 code |
| `country_name` | string | Human-readable country name |
| `flag_emoji` | string | Unicode flag emoji |
| `count` | integer | Number of approved libraries |

Each **time series point** contains:

| Field | Type | Description |
|-------|------|-------------|
| `period` | string | Date label (`YYYY-MM-DD`) |
| `cumulative_count` | integer | Running total of approved libraries |

### Example request

=== "curl"

    ```bash
    curl https://bookcorners.org/api/v1/statistics/
    ```

=== "Python"

    ```python
    import httpx

    response = httpx.get("https://bookcorners.org/api/v1/statistics/")
    data = response.json()
    print(f"Total libraries: {data['total_approved']}")
    ```

### Example response

```json
{
  "total_approved": 350,
  "total_with_image": 280,
  "top_countries": [
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
    }
  ],
  "cumulative_series": [
    {"period": "2025-01-01", "cumulative_count": 10},
    {"period": "2025-02-01", "cumulative_count": 45},
    {"period": "2025-03-01", "cumulative_count": 120},
    {"period": "2025-04-01", "cumulative_count": 350}
  ],
  "granularity": "monthly"
}
```

### Notes

- Data is cached for 5 minutes. Changes to library approvals may take up to 5 minutes to appear.
- The `granularity` field is `"daily"` when the oldest approved library was created within the last 90 days, and `"monthly"` otherwise.
- The `top_countries` list is capped at 10 entries, ordered by count descending.
