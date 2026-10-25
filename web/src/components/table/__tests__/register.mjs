// Lets `node --test` import the Table's TypeScript modules directly (Node 24 strips types natively;
// this only adds the ".ts" extension that Vite-style imports leave out).
import { register } from "node:module";

register("./resolve-ts.mjs", import.meta.url);
