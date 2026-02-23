// PostCSS configuration - CommonJS format (.cjs extension required)
// because package.json has "type": "module" which makes Node treat .js as ESM.
// Using .cjs forces CommonJS regardless of the "type" field.
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
