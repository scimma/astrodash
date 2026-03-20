# User Models

Manage user-uploaded models and metadata.

## Endpoints

```text
POST /api/v1/upload-model
GET  /api/v1/models
GET  /api/v1/models/{model_id}
DELETE /api/v1/models/{model_id}
PUT /api/v1/models/{model_id}
GET  /api/v1/models/owner/{owner}
```

## Upload Model

```text
POST /api/v1/upload-model
Content-Type: multipart/form-data
```

Fields:

- `file`: .pth or .pt TorchScript model (required)
- `class_mapping`: JSON string mapping class name -> index (required)
- `input_shape`: JSON list shape or list of shapes (required)
- `name`, `description`, `owner`: optional metadata

Response:

```json
{
  "status": "success",
  "message": "Model uploaded and validated successfully.",
  "model_id": "...",
  "model_filename": "model.pth",
  "class_mapping": {"Ia": 0, "II": 1},
  "output_shape": [1, 5],
  "input_shape": [1, 1024],
  "model_info": {"parameters": 1234567}
}
```

## List Models

```text
GET /api/v1/models
```

Returns an array of models including `class_mapping`, `input_shape`, and `model_filename` when available.

## Get/Update/Delete Model

- `GET /api/v1/models/{model_id}`: returns detailed `model_info`
- `PUT /api/v1/models/{model_id}`: update `name` and/or `description`
- `DELETE /api/v1/models/{model_id}`: remove model and associated files

## Notes

- Use the returned `model_id` with `/api/v1/process` by adding `model_id` as a separate form field to classify with a user model.
- Validation ensures output dimension matches the class mapping size.
