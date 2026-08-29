// Vite's `?raw` suffix imports a file's text. The app's tsconfig does not
// pull in vite/client, so declare just the one form the Help tab uses.
declare module "*?raw" {
  const contents: string;
  export default contents;
}
