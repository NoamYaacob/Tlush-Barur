// Tailwind CSS v3 configuration - CommonJS format (.cjs extension required)
// because package.json has "type": "module".
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
