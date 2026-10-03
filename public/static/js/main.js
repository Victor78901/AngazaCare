document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-password-toggle]").forEach((toggle) => {
        const passwordField = document.getElementById(toggle.getAttribute("aria-controls"));
        if (!passwordField) {
            return;
        }

        toggle.addEventListener("click", () => {
            const isVisible = passwordField.type === "password";
            passwordField.type = isVisible ? "text" : "password";
            toggle.setAttribute("aria-pressed", String(isVisible));
            const label = isVisible ? toggle.dataset.hideLabel : toggle.dataset.showLabel;
            toggle.setAttribute("aria-label", label);
            toggle.setAttribute("title", label);
            toggle.classList.toggle("is-visible", isVisible);
        });
    });

    const passwordField = document.querySelector("[data-password-policy]");
    if (passwordField) {
        const feedback = document.getElementById(passwordField.dataset.passwordFeedback);
        const confirmationField = passwordField.form.querySelector('[name="confirm_password"]');
        const complexityPattern = /^(?=.*[a-z])(?=.*[A-Z])(?=.*[0-9])(?=.*[^A-Za-z0-9\s]).{12,128}$/;

        const validatePassword = () => {
            const password = passwordField.value;
            let message = "";
            if (password.length > 0 && password.length < 12) {
                message = passwordField.dataset.passwordMinMessage;
            } else if (password.length > 128) {
                message = passwordField.dataset.passwordMaxMessage;
            } else if (password && !complexityPattern.test(password)) {
                message = passwordField.dataset.passwordComplexityMessage;
            }

            passwordField.setCustomValidity(message);
            if (feedback) {
                feedback.textContent = message || (password ? passwordField.dataset.passwordValidMessage : passwordField.dataset.passwordHint);
            }
            if (confirmationField) {
                confirmationField.setCustomValidity(
                    confirmationField.value && confirmationField.value !== password
                        ? passwordField.dataset.passwordMismatchMessage
                        : ""
                );
            }
        };

        passwordField.addEventListener("input", validatePassword);
        if (confirmationField) {
            confirmationField.addEventListener("input", validatePassword);
        }
        validatePassword();
    }

    const stressField = document.getElementById("stress_level");
    const stressLabel = document.getElementById("stressValue");
    if (stressField && stressLabel) {
        const updateStress = () => {
            stressLabel.innerText = stressField.value;
        };
        stressField.addEventListener("input", updateStress);
        updateStress();
    }
});
