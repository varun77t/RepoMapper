// cytoscape-fcose ships no type definitions. The layout options are passed as
// a plain object to cy.layout(), so a minimal declaration is enough.
declare module "cytoscape-fcose" {
  import type { Ext } from "cytoscape";
  const ext: Ext;
  export default ext;
}
