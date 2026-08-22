# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/README.md) uses [Babel](https://babeljs.io/) for Fast Refresh
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react-swc) uses [SWC](https://swc.rs/) for Fast Refresh

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type aware lint rules:

- Configure the top-level `parserOptions` property like this:

```js
export default tseslint.config({
  languageOptions: {
    // other options...
    parserOptions: {
      project: ['./tsconfig.node.json', './tsconfig.app.json'],
      tsconfigRootDir: import.meta.dirname,
    },
  },
})
```

- Replace `tseslint.configs.recommended` to `tseslint.configs.recommendedTypeChecked` or `tseslint.configs.strictTypeChecked`
- Optionally add `...tseslint.configs.stylisticTypeChecked`
- Install [eslint-plugin-react](https://github.com/jsx-eslint/eslint-plugin-react) and update the config:

```js
// eslint.config.js
import react from 'eslint-plugin-react'

export default tseslint.config({
  // Set the react version
  settings: { react: { version: '18.3' } },
  plugins: {
    // Add the react plugin
    react,
  },
  rules: {
    // other rules...
    // Enable its recommended rules
    ...react.configs.recommended.rules,
    ...react.configs['jsx-runtime'].rules,
  },
})
```

## Testing

Unit tests (Vitest + Testing Library):

```bash
npm run test
```

E2E screenshot tests (Playwright, hermetic — no live backend, API responses are
mocked via `page.route`):

```bash
npm run test:e2e
```

`e2e/fixtures.ts` mocks every `/api/v1/**` call the pages make. `e2e/usage-page.spec.ts`
and `e2e/session-detail.spec.ts` render `UsagePage` and `SessionDetailPage`, assert the
key elements (hero, chart, dev roster, spike callout, synthesis card, collapsed detail),
and capture a full-page screenshot to `e2e/screenshots/*.png` as a test artifact. Both
run as part of `make test-frontend` (chained via `make test-e2e`) in the presubmit gate.

Add a screenshot for a new page by writing another `e2e/*.spec.ts` alongside these two,
reusing `mockApi` from `fixtures.ts` for hermetic route mocking.
