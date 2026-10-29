/** @type {import("next").NextConfig} */
const nextConfig = {
  // React StrictMode double-invokes effects in development. With
  // @xyflow/react 12.8 the drag handlers are attached in an effect whose
  // ordering is fragile under that double invocation, which silently disables
  // node dragging in dev. Production is single-invoked, but keeping dev and
  // prod behaviour identical matters more than the extra dev warnings.
  reactStrictMode: false,
  poweredByHeader: false,
};

export default nextConfig;
