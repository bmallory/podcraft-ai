# 1. Use an official AWS Lambda base image
FROM public.ecr.aws/lambda/python:3.12

# 2. Copy requirements.txt and install dependencies
COPY requirements.txt ${LAMBDA_TASK_ROOT}
RUN pip install -r requirements.txt

# 3. Copy your function code
COPY app.py ${LAMBDA_TASK_ROOT}/

# 4. Set the CMD to your handler (file_name.function_name)
CMD [ "app.handler" ]
