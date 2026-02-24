/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./templates/**/*.html",
    "./**/templates/**/*.html",
  ],
  safelist: [
    "input",
    "input-bordered",
  ],
  theme: {
    extend: {},
  },
  plugins: [require("daisyui")],
}
