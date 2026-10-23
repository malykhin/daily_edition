# The Daily Edition

A personal, mobile-first newspaper built with Astro and published as a static site on GitHub Pages.

## Run locally

```bash
npm install
npm run dev
```

## Add an edition

Create a new JSON file at `content/editions/YYYY-MM-DD.json` following the sample schema. The latest edition with `status: "published"` becomes the front page. Committing a new edition automatically triggers a GitHub Pages deployment once Pages is configured.

Sample editions are **not live reporting**. All market data, weather, events and news must be researched and verified before publishing.

Keep source URLs as plain text in edition JSON. The site renders URLs in narrative text as labeled hyperlinks (for example, Open-Meteo or Environment Canada), preserving their full destinations without displaying long query strings. No HTML or Markdown is required.

Weather is displayed as a compact four-day forecast with Celsius highs/lows and labeled source links. Highs and lows use separate aligned rows across all four days. Daily conditions appear below temperatures, using sourced weather descriptions. The publisher fetches Open-Meteo weather codes as well as highs/lows; older editions without conditions remain unchanged. Older editions without dated forecast pairs show their available daily temperatures instead.

The Events page combines upcoming arts/culture events and technical meetups from the latest edition, hides past dated listings, and shows an explicit empty state when nothing is verified.

## Publish

In repository Settings → Pages, choose **GitHub Actions** as the build/deploy source. The workflow uses `withastro/action` and `actions/deploy-pages`.

Site: https://malykhin.github.io/daily_edition/

## Privacy

This repository and GitHub Pages site are public. Do not put private financial, calendar, personal or authentication data in edition files.

GitHub Pages source configured to GitHub Actions on October 18, 2026. A commit to main triggers a deployment.
