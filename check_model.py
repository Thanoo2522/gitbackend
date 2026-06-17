import tensorflow as tf

interpreter = tf.lite.Interpreter(model_path="model.tflite")
interpreter.allocate_tensors()

print("INPUT")
print(interpreter.get_input_details())

print("OUTPUT")
print(interpreter.get_output_details())