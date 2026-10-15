// Decode a COCO run-length encoded mask, with no dependency.
//
// GWHS stores every object as the compressed counts string COCO defines, and hosted SAM
// endpoints answer masks in the same format. The runs are read over the mask flattened
// column-major (COCO's Fortran order), so the flat result indexes as row + column * height.
rleDecode = (counts, height, width) => {
  const runs = [];
  let i = 0;
  while (i < counts.length) {
    let value = 0, shift = 0, chunk;
    do {
      chunk = counts.charCodeAt(i++) - 48;
      value |= (chunk & 0x1f) << shift;
      shift += 5;
    } while (chunk & 0x20);
    if (chunk & 0x10) value |= -1 << shift;   // sign-extend the last chunk
    runs.push(value + (runs.length > 2 ? runs[runs.length - 2] : 0));
  }
  const mask = new Uint8Array(height * width);
  let position = 0;
  for (let run = 0; run < runs.length; run++) {
    if (run % 2) mask.fill(1, position, position + runs[run]);
    position += runs[run];
  }
  return mask;
};
