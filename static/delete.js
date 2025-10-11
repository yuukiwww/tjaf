function songDelete(submit) {
  const id = decodeURIComponent(submit.dataset.id);

  fetch("/api/delete", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      id,
    })
  })
    .then((res) => res.text())
    .then((text) => {
      alert(text);
    });
}
