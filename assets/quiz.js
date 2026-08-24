document.querySelectorAll("[data-quiz]").forEach((quiz) => {
  const feedback = quiz.querySelector(".feedback");
  quiz.querySelectorAll("button.answer").forEach((button) => {
    button.addEventListener("click", () => {
      quiz.querySelectorAll("button.answer").forEach((item) => {
        item.classList.remove("correct", "wrong");
        item.setAttribute("aria-pressed", "false");
      });
      const correct = button.dataset.correct === "true";
      button.classList.add(correct ? "correct" : "wrong");
      button.setAttribute("aria-pressed", "true");
      feedback.textContent = correct
        ? button.dataset.success
        : button.dataset.retry;
    });
  });
});
