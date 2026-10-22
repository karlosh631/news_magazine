import { existsSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import ts from "typescript";

/**
 * Loading a React component so that a test can look at what it renders.
 *
 * WHY THIS EXISTS
 * ---------------
 * Playwright rewrites the JSX in every file it loads into its own
 * element objects, so that its component-testing feature can render
 * them. Those objects are not React elements, and React refuses to
 * render them: "Objects are not valid as a React child".
 *
 * That means `renderToString(<SomeComponent />)` cannot work in a
 * spec, which is a shame, because the accessibility contract of a
 * dialog IS its rendered markup: `role="dialog"`, `aria-modal`,
 * `aria-labelledby`, a named close button. None of that can be pinned
 * by testing the functions that feed it, because the markup is
 * written in the component.
 *
 * So the component is compiled here instead, with the TypeScript
 * compiler that is already a dependency of this project, and loaded
 * through a small resolver that understands the `@/` alias. What comes
 * out is the real component, and `renderToString` gives back the real
 * markup — the same bytes the browser would receive.
 *
 * The alternative was a browser spec, and this repository's CI does
 * not install one: the suite is deliberately pure functions over pure
 * modules so that it runs in seconds. This keeps it that way.
 */

/** The root of the web app. */
const APP_ROOT = resolve(__dirname, "..", "..");

/**
 * Node's own resolver, anchored at the app so that `react` and
 * `next` are found in the app's node_modules.
 */
const nodeRequire = createRequire(resolve(APP_ROOT, "package.json"));

/** The extensions a local import may carry. */
const EXTENSIONS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"];

/** Find the file an import names. */
function resolveLocal(base: string): string {
  const candidates = [
    base,
    ...EXTENSIONS.map((extension) => `${base}${extension}`),
    ...EXTENSIONS.map((extension) => resolve(base, `index${extension}`)),
  ];
  for (const candidate of candidates) {
    if (existsSync(candidate) && statSync(candidate).isFile()) {
      return candidate;
    }
  }
  throw new Error(`Cannot resolve "${base}" from the app root.`);
}

/** Compile one file and load it, loading whatever it imports in turn. */
function loadFile(file: string): unknown {
  if (file.endsWith(".json")) {
    return JSON.parse(readFileSync(file, "utf8"));
  }

  const source = readFileSync(file, "utf8");
  const { outputText } = ts.transpileModule(source, {
    fileName: file,
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      // The real React JSX runtime, not Playwright's.
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
      resolveJsonModule: true,
    },
  });

  const module = { exports: {} as unknown };
  const localRequire = (id: string): unknown => {
    if (id.startsWith("@/")) {
      return loadFile(resolveLocal(resolve(APP_ROOT, id.slice(2))));
    }
    if (id.startsWith(".")) {
      return loadFile(resolveLocal(resolve(dirname(file), id)));
    }
    return nodeRequire(id);
  };

  const factory = new Function(
    "exports",
    "require",
    "module",
    "__filename",
    "__dirname",
    outputText
  );
  factory(module.exports, localRequire, module, file, dirname(file));

  return module.exports;
}

/**
 * Load a component from the app by its `@/` path.
 *
 *   const { StoryDialog } = loadComponent<typeof import("@/components/StoryDialog")>(
 *     "@/components/StoryDialog"
 *   );
 */
export function loadComponent<T>(specifier: string): T {
  const file = resolveLocal(
    specifier.startsWith("@/")
      ? resolve(APP_ROOT, specifier.slice(2))
      : resolve(APP_ROOT, specifier)
  );
  return loadFile(file) as T;
}
