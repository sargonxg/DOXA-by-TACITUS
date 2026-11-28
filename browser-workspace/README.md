# Signed-in DOXA browser workspace

[Open DOXA](https://doxa.tacitus.me/) · [GitHub](https://github.com/sargonxg/DOXA-by-TACITUS) · [Hugging Face research](https://huggingface.co/spaces/Giuliocc/doxa-lab)

The hosted product requires email verification through Cloudflare Access. Its Worker also verifies the signed assertion, issuer, audience, lifetime and account before serving HTML, scripts, models or the scorecard. Forged identity headers grant no access. The public company site and Hugging Face pages explain the work and publish research assets.

Document analysis runs in the browser. Review projects retain original files in IndexedDB under an account-specific namespace. A different signed-in account does not automatically open the previous account's projects. This separates normal application use; it does not encrypt browser storage or protect it from someone with access to the same browser profile or developer tools. Export a portable project for backup or handover. This browser product does not offer server document storage or team synchronization.

`web/` preserves and extends the released document-review source. `client/` contains its reproducible static build. `release.json` pins the imported source archive, model/runtime assets and scorecard. The existing installed Python application at the repository root is separate and retains its managed-workspace deployment options.

## Build and verify

```sh
cd browser-workspace/web
npm ci --ignore-scripts
npm run build
cd ../..
DOXA_CLIENT_REVISION=<commit-containing-browser-workspace/client> node scripts/build-browser-workspace.mjs
node --test --test-isolation=none tests/browser-workspace-auth.test.mjs
```

The Worker serves the small entry assets directly. Larger client modules load through the authenticated Worker from the exact GitHub commit. Model, OCR and reference assets are pinned to the existing public release. No user cookies or credentials are forwarded upstream. Generated Worker assets are ignored by Git.

For the isolated browser acceptance test:

```sh
python scripts/prepare-browser-fixtures.py /tmp/doxa-qa-assets
DOXA_QA_ASSETS=/tmp/doxa-qa-assets node tests/serve_browser_workspace.mjs
```

In a separate terminal, run `python tests/browser_product_auth.py`. The fixture accepts two synthetic account identifiers on loopback only. It exercises the real client, a local baseline audit, source retention, saved projects and account switching. It is never imported into the production Worker. These checks do not establish model accuracy or a completed human email sign-in.

## Release boundary

Deploy `infra/cloudflare/wrangler.browser.jsonc` with the configured Access audience. Keep Workers.dev and preview URLs disabled. Confirm the reusable verified-email policy is attached and the old redirect to the public Space is disabled. Guest requests to every product path must lead to sign-in; a missing/invalid Worker identity must receive 401. Preserve public GitHub and Hugging Face research links. Roll back the Worker to a verified protected version; do not restore an unauthenticated product redirect.
