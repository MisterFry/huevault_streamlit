Place the deployable HueVault wheel in this directory before packaging or
deploying the Databricks app.

Expected artifact:

- `huevault-0.1.1-py3-none-any.whl`

Expected flow:
1. Build the HueVault wheel from the sibling repo.
2. Copy or emit the wheel into `wheels/`.
3. Keep `requirements.txt` pointed at the exact filename in this folder.
4. Deploy the app to Databricks Apps.

The Databricks packaging pattern for this repo intentionally keeps the wheel in
source control so the app bundle is self-contained.
