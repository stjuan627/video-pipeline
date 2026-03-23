(() => {
  const matches = Array.from(document.querySelectorAll('[data-id]'));
  const firstMatch = matches[0] ?? null;

  return {
    firstDataId: firstMatch ? firstMatch.getAttribute('data-id') : null,
    found: Boolean(firstMatch),
    tagName: firstMatch ? firstMatch.tagName : null,
    totalMatches: matches.length,
    allDataIds: matches
      .map((node) => node.getAttribute('data-id'))
      .filter((value) => typeof value === 'string' && value.length > 0)
      .slice(0, 10)
  };
})();
