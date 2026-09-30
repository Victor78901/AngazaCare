document.addEventListener("DOMContentLoaded", function () {
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
