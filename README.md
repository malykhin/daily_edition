# The Daily Edition

A personal, mobile-first newspaper built with Astro and published as a static site on GitHub Pages.

## Run locally

```bash
npm install
npm run dev
```

## Add an edition

Create a new JSON file at `content/editions/YYYY-MM-DD.json` following the sample schema. The latest date becomes the front page. Committing a new edition automatically triggers a GitHub Pages deployment once Pages is configured.

The sample edition is **not live reporting**. Until publishing is automated, all market data, weather, event and news fields must be researched and verified before publishing.

## Publish

In repository Settings → Pages, choose **GitHub Actions** as the build/deploy source. The workflow uses `withastro/action` and `actions/deploy-pages`.

Site: https://malykhin.github.io/daily_edition/

## Privacy

This repository and GitHub Pages site are public. Do not put private financial, calendar, personal or authentication data in edition files.
