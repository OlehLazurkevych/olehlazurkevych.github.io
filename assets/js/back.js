(() => {
    const a = document.getElementById("backLink");
    if (!a) return;
  
    a.addEventListener("click", (e) => {
      // If user has history, go back (keeps scroll position automatically)
      if (window.history.length > 1) {
        e.preventDefault();
        window.history.back();
      }
      // else fallback to href (home)
    });
  })();
  