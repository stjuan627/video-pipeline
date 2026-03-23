async function performJimengUpload() {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const bridge = document.getElementById("real-bridge");

  if (!bridge || !bridge.files.length) {
    return "No files in bridge";
  }

  const target =
    document.querySelector(".prompt-editor-E3Iuyy") ||
    document.querySelector(".reference-upload-h7tmnr") ||
    document.querySelector('[class*="references"]') ||
    document.querySelector('[class*="editor"]');

  if (!target) {
    return "Upload target not found";
  }

  const dataTransfer = new DataTransfer();
  for (const file of bridge.files) {
    dataTransfer.items.add(file);
  }

  const dropEvent = new DragEvent("drop", {
    bubbles: true,
    cancelable: true,
    dataTransfer,
  });

  target.dispatchEvent(dropEvent);

  for (let i = 0; i < 20; i += 1) {
    await wait(1000);
    const previews = document.querySelectorAll('[class*="reference-item"] img, .reference-item-container img');
    if (previews.length >= bridge.files.length) {
      return "Upload and Preview Verified";
    }
  }

  return "Dropped, but Preview check timed out";
}
