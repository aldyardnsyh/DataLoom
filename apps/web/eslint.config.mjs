import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({
  baseDirectory: import.meta.dirname,
});

const eslintConfig = [
  ...compat.extends("next", "next/core-web-vitals"),
  {
    ignores: [".next/**", "node_modules/**", "mock/**", "qa-artifacts/**"],
  },
];

export default eslintConfig;
