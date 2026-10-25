// Node module resolve hook: "./format" -> "./format.ts" (or .tsx) for relative, extensionless imports.
const HAS_EXT = /\.[cm]?[jt]sx?$/;

export async function resolve(specifier, context, nextResolve) {
  if ((specifier.startsWith("./") || specifier.startsWith("../")) && !HAS_EXT.test(specifier)) {
    for (const ext of [".ts", ".tsx"]) {
      try {
        return await nextResolve(specifier + ext, context);
      } catch {
        // try the next extension
      }
    }
  }
  return nextResolve(specifier, context);
}
