# Line List

Retrieve spectral line lists for plotting and analysis.

## Endpoints

```text
GET /api/v1/line-list
GET /api/v1/line-list/elements
GET /api/v1/line-list/element/{element}
GET /api/v1/line-list/filter?min_wavelength=...&max_wavelength=...
```

## Descriptions

- `GET /api/v1/line-list`: Returns a dictionary mapping element/ion names to arrays of wavelengths.
- `GET /api/v1/line-list/elements`: Returns a list of available element/ion names.
- `GET /api/v1/line-list/element/{element}`: Returns wavelengths for a specific element.
- `GET /api/v1/line-list/filter`: Returns a filtered dictionary within a wavelength range.

## Responses

### Example: Full line list

```json
{
  "H": [6563.0, 4861.0, "..."],
  "He": [5876.0, "..."]
}
```

### Example: Elements

```json
{
  "elements": ["H", "He", "Ca", "Si", "..."]
}
```

### Example: Single element

```json
{
  "element": "H",
  "wavelengths": [6563.0, 4861.0],
  "count": 2
}
```

## Notes

- Use these endpoints to overlay line markers on spectrum plots.
- For valid ranges and elements, see `/api/v1/line-list/elements` first.
