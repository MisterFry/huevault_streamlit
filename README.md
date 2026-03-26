# huevault_streamlit

Streamlit test app for the HueVault colour registry and similarity service.

## Local setup

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

For source-based local development, this app can also import the sibling
`../huevault` repo directly through its bootstrap path helper.

## Databricks Apps packaging

This repo follows the Databricks app pattern used in the sibling Streamlit app:

- `app.yaml` defines the Databricks app command
- `.streamlit/config.toml` provides headless Streamlit settings
- `wheels/` stores bundled private dependency wheels

The HueVault package is expected to be bundled as:

- `wheels/huevault-0.1.1-py3-none-any.whl`

`requirements.txt` points directly at that wheel so the Databricks app bundle is
self-contained at deploy time.

## Deployment flow

1. Build the HueVault wheel from the sibling repo.
2. Place `huevault-0.1.1-py3-none-any.whl` in `wheels/`.
3. Verify `requirements.txt` still references that exact filename.
4. Deploy the repo as a Databricks App.

## Sample data

Example fixture files are included in `sample_data/`.
