# phidata/pipeline.py

from phidata import App
from phidata.pipeline import Pipeline, Step

# The name of the image you built (e.g., text-generation-agent:latest)
CONTAINER_IMAGE = "my-ocr-bot:latest"

# Step: Run your main script inside the container
main_step = Step(
    name="run-adb-ocr",
    image=CONTAINER_IMAGE,
    command=["python", "main.py"],
    env={
        "NANOGPT_API_KEY": "{{ env.NANOGPT_API_KEY }}",
        "NANOGPT_BASE_URL": "{{ env.NANOGPT_BASE_URL }}",
        "NANOGPT_MODEL": "{{ env.NANOGPT_MODEL }}",
    },
    # You could also pass volumes, etc. if your pipeline needs them
)

# Define a pipeline with one step
my_pipeline = Pipeline(name="ocr-pipeline", steps=[main_step])

# Create an App that references the pipeline
app = App(name="adb-ocr-app", pipelines=[my_pipeline])
