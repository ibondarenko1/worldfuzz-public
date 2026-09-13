// Keep large integer literals (e.g. nanosecond timestamps) as decimal display text.
// Strings are matched as complete tokens, so digits inside them are never changed.
// Downloads use the original HTTP bytes; this representation is only for the UI.
export function parseResponseText(raw){
 return JSON.parse(raw.replace(/"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,token=>
  /^-?\d+$/.test(token)&&!Number.isSafeInteger(Number(token))?JSON.stringify(token):token));
}
